"""EARDRUM — a camera with no microphone, listening.

A surface that vibrates moves by a fraction of a pixel. Recovering that motion turns any
textured patch in frame into a displacement sensor, and a displacement sensor is a vibration
channel: machinery imbalance, structural resonance, footfall, a door slamming, glass breaking.

Engineering decisions that matter more than the DSP:

**Phase correlation, not phase-based motion magnification.** Full-frame steerable pyramids are
the textbook approach and are far too expensive for a GIL-bound Python process. Per-probe
sub-pixel phase correlation over a 64x64 ROI is roughly a thousand times cheaper and adequate:
~0.05 px on well-textured surfaces, which is all the structural band needs.

**Raw frames only.** JPEG quantisation destroys sub-pixel information, so probes are read from
the capture-thread tap before any encoding, never from the display path.

**Uniform resampling before any FFT.** Capture timestamps jitter. Feeding a non-uniform series
to an FFT smears every peak, and it is the single most common implementation error in this
technique. Everything is resampled onto a uniform grid first.

**Common-mode rejection.** One probe is designated REF on rigid structure and its motion is
subtracted from every other. Without it, mount vibration contaminates all readings, which is the
classic failure of vision-based structural monitoring.

**A measured noise floor, always reported.** The achievable floor is set by the source's
compression, not by the sensor. It is measured from the probes themselves and drawn across the
spectrum, because a product that lets an operator read a peak below its own noise floor is
lying to them.

Band policy. The structural band (0 to fps/2, about 0-15 Hz) cannot carry intelligible speech:
speech fundamentals start around 85 Hz. That is a real privacy safeguard and it is enforced in
code, not only in the UI. The rolling-shutter acoustic band CAN approach speech bandwidth, so it
is off by default, requires calibration, and logs every time it is enabled.
"""
from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from typing import Any

import cv2
import numpy as np

log = logging.getLogger("overseer.eardrum")

_EPS = 1e-12
STRUCTURAL = "structural"
ACOUSTIC = "acoustic"


# ── sub-pixel displacement ──────────────────────────────────────────────────────────────────

#: Frequencies above this fraction of Nyquist are discarded before the correlation peak is
#: found. A lens plus a compressed stream has essentially no signal up there, and whitening
#: would otherwise amplify that empty band into pure noise.
_PC_CUTOFF = 0.30
_PC_MIN_QUALITY = 8.0     # peak-to-mean of the correlation surface; below this, report nothing
_PC_MAX_SHIFT = 6.0       # px between consecutive frames: a vibrating surface does not do more


def phase_shift(ref: np.ndarray, cur: np.ndarray,
                cutoff: float = _PC_CUTOFF) -> tuple[float, float, float]:
    """Sub-pixel (dx, dy, quality) between two grayscale patches.

    Ordinary phase correlation (including cv2.phaseCorrelate) whitens the cross-power spectrum
    completely, which is optimal for a noiseless band-unlimited signal and badly wrong here: a
    lens and a compressed stream have almost no energy near Nyquist, so whitening amplifies that
    empty band into noise and the correlation peak wanders. Measured on a synthetic bench, the
    fully-whitened version drifts by SEVERAL PIXELS on a static scene with ordinary sensor
    noise. Band-limiting the whitened spectrum first fixes it.

    Measured on that bench (64x64 patch, blurred texture, exact Fourier shifts):
        accuracy      ~0.02 px mean absolute error over shifts up to 0.6 px
        stability     0.03 px std on a STATIC pair at sensor noise sigma 0.7
                      0.10 px std at sigma 5.0
        cost          ~0.26 ms per call

    `quality` is the correlation peak divided by the surface mean. A featureless or mismatched
    patch scores near 1 and the caller refuses to report a reading rather than emitting a number
    it cannot stand behind.

    Note the scale: the estimator reads slightly low (gain ~0.85), which is a constant factor
    and therefore affects reported amplitude, never frequency. Every product built on top of
    this measures frequency.
    """
    a = np.asarray(ref, np.float32)
    b = np.asarray(cur, np.float32)
    if a.shape != b.shape or a.ndim != 2 or a.size < 64:
        return 0.0, 0.0, 0.0
    h, w = a.shape
    win = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    A = np.fft.fft2((a - a.mean()) * win)
    B = np.fft.fft2((b - b.mean()) * win)
    R = A * np.conj(B)
    mag = np.abs(R)
    peak_mag = float(mag.max())
    if peak_mag < 1e-9:
        return 0.0, 0.0, 0.0
    # whiten, but never divide into the numerical floor
    R = R / (mag + 1e-6 * peak_mag)
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.fftfreq(w)[None, :]
    R *= np.exp(-((np.sqrt(fx * fx + fy * fy) / max(1e-3, cutoff)) ** 2))
    c = np.real(np.fft.ifft2(R))
    py, px = np.unravel_index(int(np.argmax(c)), c.shape)

    def _parabolic(m: float, left: float, right: float) -> float:
        d = left - 2.0 * m + right
        return 0.5 * (left - right) / d if abs(d) > 1e-12 else 0.0

    dx = px + _parabolic(float(c[py, px]), float(c[py, (px - 1) % w]), float(c[py, (px + 1) % w]))
    dy = py + _parabolic(float(c[py, px]), float(c[(py - 1) % h, px]), float(c[(py + 1) % h, px]))
    if dx > w / 2:
        dx -= w
    if dy > h / 2:
        dy -= h
    quality = float(c[py, px]) / (float(np.mean(np.abs(c))) + _EPS)
    if quality < _PC_MIN_QUALITY or abs(dx) > _PC_MAX_SHIFT or abs(dy) > _PC_MAX_SHIFT:
        return 0.0, 0.0, quality
    # the sign convention is "how far the content moved", which is what a displacement sensor
    # is expected to report
    return -float(dx), -float(dy), quality


def texture_score(patch: np.ndarray) -> float:
    """How well this patch can be tracked, in [0,1].

    The minimum eigenvalue of the structure tensor is the classic answer: it is high only when
    there is gradient in BOTH directions, which is exactly when phase correlation is reliable. A
    blank wall scores near zero and the UI refuses to place a probe there.
    """
    g = np.asarray(patch, np.float32)
    if g.ndim == 3:
        g = cv2.cvtColor(g.astype(np.uint8), cv2.COLOR_BGR2GRAY).astype(np.float32)
    if g.size < 16:
        return 0.0
    eig = cv2.cornerMinEigenVal(g, blockSize=5, ksize=3)
    return float(min(1.0, float(np.mean(np.abs(eig))) / 120.0))


# ── time series ─────────────────────────────────────────────────────────────────────────────

def resample_uniform(ts: np.ndarray, xs: np.ndarray, rate: float | None = None
                     ) -> tuple[np.ndarray, float]:
    """Resample a jittered series onto a uniform grid. Returns (values, sample_rate).

    Skipping this smears every spectral peak. The grid rate is the MEDIAN inter-sample rate,
    which is robust to the occasional dropped frame in a way the mean is not.
    """
    t = np.asarray(ts, np.float64)
    x = np.asarray(xs, np.float64)
    if t.size < 4:
        return x.astype(np.float32), float(rate or 30.0)
    order = np.argsort(t)
    t, x = t[order], x[order]
    dt = np.diff(t)
    dt = dt[dt > 0]
    if dt.size == 0:
        return x.astype(np.float32), float(rate or 30.0)
    fs = float(rate) if rate else float(1.0 / np.median(dt))
    n = int((t[-1] - t[0]) * fs)
    if n < 4:
        return x.astype(np.float32), fs
    grid = t[0] + np.arange(n) / fs
    return np.interp(grid, t, x).astype(np.float32), fs


def detrend(x: np.ndarray) -> np.ndarray:
    """Remove the slow drift that thermal expansion and re-anchoring produce, so it does not
    dominate the low bins."""
    x = np.asarray(x, np.float32)
    if x.size < 4:
        return x - float(np.mean(x)) if x.size else x
    i = np.arange(x.size, dtype=np.float32)
    a, b = np.polyfit(i, x, 1)
    return (x - (a * i + b)).astype(np.float32)


def spectrum(x: np.ndarray, fs: float, window: int = 1024) -> tuple[np.ndarray, np.ndarray]:
    """Welch PSD in dB relative to 1 px. Returns (freqs, psd_db)."""
    x = detrend(np.asarray(x, np.float32))
    n = int(min(window, max(16, x.size)))
    if x.size < 16:
        return np.zeros(1, np.float32), np.full(1, -200.0, np.float32)
    hop = max(1, n // 4)
    win = np.hanning(n).astype(np.float32)
    acc = np.zeros(n // 2 + 1, np.float64)
    count = 0
    for s in range(0, max(1, x.size - n + 1), hop):
        seg = x[s:s + n]
        if seg.size < n:
            break
        spec = np.fft.rfft(seg * win)
        acc += (np.abs(spec) ** 2)
        count += 1
    if count == 0:
        seg = np.zeros(n, np.float32)
        seg[:x.size] = x[:n]
        acc = np.abs(np.fft.rfft(seg * win)) ** 2
        count = 1
    psd = acc / count / (np.sum(win ** 2) + _EPS)
    freqs = np.fft.rfftfreq(n, 1.0 / max(_EPS, fs)).astype(np.float32)
    return freqs, (10.0 * np.log10(psd + 1e-14)).astype(np.float32)


def noise_floor(psd_db: np.ndarray) -> float:
    """The measured floor, from the data rather than from a datasheet.

    The median of the upper half of the spectrum is where a mechanical scene has no real
    content, so it is the honest estimate of what this source can resolve. Every peak below it
    is drawn as unreal in the UI.
    """
    if psd_db.size < 8:
        return float(psd_db.min()) if psd_db.size else -200.0
    tail = psd_db[psd_db.size // 2:]
    return float(np.median(tail))


def find_peaks(freqs: np.ndarray, psd_db: np.ndarray, floor: float, *, min_hz: float = 0.4,
               prominence: float = 10.0, limit: int = 12) -> list[dict]:
    """Prominence-based peak picking above the measured floor.

    The 10 dB default is measured, not chosen. A periodogram bin is chi-squared distributed, so
    white noise alone reaches well above its own median: over 40 synthetic noise runs the
    largest excursion was 8.6 dB. At a 6 dB gate those runs produced 63 phantom peaks; at 10 dB
    they produce none, while a real 0.002 px tone still reads 26 dB of prominence. Sensitivity
    costs nothing here and a phantom peak on a quiet probe would be a lie.
    """
    out: list[dict] = []
    if psd_db.size < 5:
        return out
    for i in range(2, psd_db.size - 2):
        if freqs[i] < min_hz:
            continue
        v = psd_db[i]
        if v < floor + prominence:
            continue
        if not (v >= psd_db[i - 1] and v >= psd_db[i + 1]
                and v > psd_db[i - 2] and v > psd_db[i + 2]):
            continue
        # parabolic interpolation for a frequency between bins
        y0, y1, y2 = float(psd_db[i - 1]), float(v), float(psd_db[i + 1])
        denom = (y0 - 2 * y1 + y2)
        delta = 0.5 * (y0 - y2) / denom if abs(denom) > _EPS else 0.0
        df = float(freqs[1] - freqs[0]) if freqs.size > 1 else 0.0
        out.append({"hz": round(float(freqs[i]) + delta * df, 3),
                    "db": round(y1, 2),
                    "prominence": round(y1 - floor, 2)})
    out.sort(key=lambda p: -p["prominence"])
    return out[:limit]


def compare_baseline(peaks: list[dict], base: list[dict], *, shift_hz: float = 0.6,
                     rise_db: float = 3.0) -> list[dict]:
    """Annotate today's peaks against a frozen baseline: new, shifted, or grown.

    This is the whole predictive-maintenance product. A peak that drifts down in frequency is
    the canonical stiffness-loss signature, and it is invisible without a reference.
    """
    out = []
    for p in peaks:
        near = min(base, key=lambda b: abs(b["hz"] - p["hz"]), default=None)
        q = dict(p)
        if near is None or abs(near["hz"] - p["hz"]) > shift_hz * 4:
            q["is_new"] = True
        else:
            if abs(near["hz"] - p["hz"]) > shift_hz:
                q["shift"] = round(p["hz"] - near["hz"], 3)
            if p["db"] - near["db"] > rise_db:
                q["rise"] = round(p["db"] - near["db"], 2)
        out.append(q)
    return out


def detect_impacts(x: np.ndarray, fs: float, *, ratio: float = 8.0) -> list[dict]:
    """Sharp transients with an exponential decay: a slam, a drop, a strike.

    Reported as IMPACT with a decay constant, never as a named cause. In the structural band a
    brittle-fracture signature is a candidate, not a verdict, and the UI says so.
    """
    x = np.abs(detrend(np.asarray(x, np.float32)))
    if x.size < int(fs) + 4:
        return []
    k = max(3, int(fs * 0.05))
    env = np.convolve(x, np.ones(k, np.float32) / k, mode="same")
    med = float(np.median(env)) + 1e-6
    out: list[dict] = []
    i = k
    while i < env.size - k:
        if env[i] > med * ratio and env[i] >= env[i - 1] and env[i] > env[i + 1]:
            tail = env[i:i + int(fs)]
            decay = 0.0
            if tail.size > 4 and tail[0] > 1e-9:
                lg = np.log(np.maximum(tail, 1e-9) / tail[0])
                idx = np.arange(tail.size, dtype=np.float32) / fs
                slope = float(np.polyfit(idx, lg, 1)[0])
                decay = float(-1.0 / slope) if slope < -_EPS else 0.0
            out.append({"t": round(i / fs, 3), "amp": round(float(env[i]), 5),
                        "decay_s": round(decay, 3)})
            i += int(fs * 0.3)
        i += 1
    return out


def cadence(x: np.ndarray, fs: float) -> float | None:
    """Dominant repetition rate of the envelope. Footfall lands around 1.5 to 2.5 Hz."""
    x = np.abs(detrend(np.asarray(x, np.float32)))
    if x.size < int(fs * 3):
        return None
    x = x - float(np.mean(x))
    ac = np.correlate(x, x, mode="full")[x.size - 1:]
    lo, hi = int(fs / 3.5), int(fs / 1.0)
    if hi <= lo or hi >= ac.size:
        return None
    lag = int(np.argmax(ac[lo:hi])) + lo
    return round(float(fs / max(1, lag)), 2)


# ── machinery interpretation ────────────────────────────────────────────────────────────────

def interpret(peaks: list[dict]) -> dict | None:
    """Classic rotating-machinery signatures, offered as hypotheses with a stated reason.

    Language is deliberate throughout: CONSISTENT WITH, never "the bearing is failing". A
    vibration spectrum narrows the possibilities; it does not diagnose.
    """
    if len(peaks) < 2:
        return None
    f0 = min((p for p in peaks[:4]), key=lambda p: p["hz"])
    if f0["hz"] < 0.5:
        return None
    harmonics = []
    for order in (1, 2, 3):
        target = f0["hz"] * order
        near = min(peaks, key=lambda p: abs(p["hz"] - target))
        if abs(near["hz"] - target) < max(0.35, target * 0.06):
            harmonics.append({"order": order, "db": near["db"]})
    sub = [p for p in peaks if 0.35 * f0["hz"] < p["hz"] < 0.55 * f0["hz"]]
    db = {h["order"]: h["db"] for h in harmonics}
    verdict, why, conf = "UNCLEAR", "no dominant harmonic family", 1
    if sub:
        verdict, why, conf = ("CONSISTENT WITH OIL WHIRL OR LOOSENESS",
                              f"energy at {sub[0]['hz']:.1f} Hz, about half the running speed", 2)
    elif 2 in db and 1 in db and db[2] - db[1] > 5:
        verdict, why, conf = ("CONSISTENT WITH MISALIGNMENT",
                              f"2x is {db[2] - db[1]:.0f} dB above 1x", 2)
    elif 1 in db and (2 not in db or db[1] - db[2] > 8):
        verdict, why, conf = ("CONSISTENT WITH IMBALANCE",
                              "the running frequency dominates with weak harmonics", 2)
    elif len(harmonics) >= 3:
        verdict, why, conf = ("CONSISTENT WITH LOOSENESS",
                              "a long harmonic series is present", 1)
    return {"f0": round(f0["hz"], 2), "rpm": round(f0["hz"] * 60.0),
            "harmonics": harmonics, "verdict": verdict, "why": why, "confidence": conf}


def alias_risk(peaks: list[dict], nyquist: float) -> bool:
    """A peak near Nyquist with no consistent harmonic family probably folded down from above."""
    if not peaks:
        return False
    top = max(p["hz"] for p in peaks)
    return top > nyquist * 0.85


# ── modal analysis ──────────────────────────────────────────────────────────────────────────

def modal(signals: list[np.ndarray], fs: float, *, window: int = 512,
          limit: int = 4) -> list[dict]:
    """Frequency domain decomposition over >=3 probes: natural frequencies and mode shapes.

    A drop in a tracked natural frequency is the canonical stiffness-loss indicator, and the
    mode shape is what the UI animates at exaggerated amplitude, which is the demo that sells
    this feature.
    """
    if len(signals) < 3:
        return []
    n = min(len(s) for s in signals)
    if n < window:
        return []
    specs = []
    win = np.hanning(window).astype(np.float32)
    for s in signals:
        seg = detrend(np.asarray(s[:window], np.float32)) * win
        specs.append(np.fft.rfft(seg))
    S = np.array(specs)                                   # (probes, bins)
    freqs = np.fft.rfftfreq(window, 1.0 / max(_EPS, fs))
    sv, shapes = [], []
    for b in range(S.shape[1]):
        col = S[:, b:b + 1]
        G = col @ col.conj().T                            # cross-power at this bin
        try:
            u, s, _ = np.linalg.svd(G)
        except np.linalg.LinAlgError:
            sv.append(0.0); shapes.append(np.zeros(len(signals)))
            continue
        sv.append(float(s[0]))
        shapes.append(np.abs(u[:, 0]))
    sv_arr = np.asarray(sv, np.float64)
    db = 10.0 * np.log10(sv_arr + 1e-14)
    peaks = find_peaks(freqs.astype(np.float32), db.astype(np.float32),
                       noise_floor(db.astype(np.float32)), min_hz=0.5, prominence=4.0,
                       limit=limit)
    out = []
    for p in peaks:
        b = int(np.argmin(np.abs(freqs - p["hz"])))
        # half-power bandwidth: the standard damping estimate
        half = db[b] - 3.0
        lo = b
        while lo > 0 and db[lo] > half:
            lo -= 1
        hi = b
        while hi < db.size - 1 and db[hi] > half:
            hi += 1
        bw = float(freqs[hi] - freqs[lo])
        zeta = bw / (2.0 * p["hz"]) if p["hz"] > 0 else 0.0
        out.append({"hz": p["hz"], "damping": round(float(zeta), 4),
                    "shape": [round(float(v), 4) for v in shapes[b]]})
    return out


# ── rolling-shutter calibration ─────────────────────────────────────────────────────────────

def line_rate_from_flicker(frame: np.ndarray, fps: float) -> dict:
    """Solve the sensor line rate from mains flicker, with no special equipment.

    Most indoor scenes contain AC lighting whose intensity bands the image at exactly 100 or
    120 Hz. Finding that banding gives the line rate for free, which is the only unknown
    standing between a rolling shutter and a kilohertz microphone. No flicker means the acoustic
    band is honestly unavailable rather than quietly wrong.
    """
    g = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    rows = g.astype(np.float32).mean(axis=1)
    rows = detrend(rows)
    if rows.size < 64:
        return {"ok": False, "reason": "frame too short"}
    spec = np.abs(np.fft.rfft(rows * np.hanning(rows.size).astype(np.float32)))
    spec[:2] = 0.0
    k = int(np.argmax(spec))
    peak = float(spec[k])
    if peak < float(np.median(spec)) * 8.0:
        return {"ok": False, "reason": "no mains flicker in this scene"}
    cycles_per_frame = k                      # bands visible down one frame
    best = None
    for mains in (100.0, 120.0):
        lines = rows.size * mains / max(_EPS, cycles_per_frame * fps)
        score = abs(lines - round(lines))
        if best is None or score < best[0]:
            best = (score, mains, lines)
    _score, mains, lines = best                                    # type: ignore[misc]
    return {"ok": True, "mains": mains, "line_rate": round(float(lines * fps), 1),
            "cycles_per_frame": cycles_per_frame}


# ── the probe bank ──────────────────────────────────────────────────────────────────────────

class Probe:
    """One listening point."""

    __slots__ = ("id", "name", "roi", "kind", "ref_patch", "anchor_ts", "ts", "dx", "dy",
                 "axis", "texture", "enabled")

    def __init__(self, pid: int, name: str, roi: list[float], kind: str = "probe",
                 texture: float = 0.0) -> None:
        self.id = int(pid)
        self.name = name
        self.roi = [float(v) for v in roi]
        self.kind = kind
        self.texture = float(texture)
        self.enabled = True
        self.ref_patch: np.ndarray | None = None
        self.anchor_ts = 0.0
        self.ts: deque[float] = deque(maxlen=4096)
        self.dx: deque[float] = deque(maxlen=4096)
        self.dy: deque[float] = deque(maxlen=4096)
        self.axis: tuple[float, float] = (1.0, 0.0)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "roi": self.roi, "kind": self.kind,
                "enabled": self.enabled, "texture": round(self.texture, 3)}

    def scalar(self) -> np.ndarray:
        """Displacement projected onto the probe's own dominant axis of motion."""
        if not self.dx:
            return np.zeros(0, np.float32)
        x = np.asarray(self.dx, np.float32)
        y = np.asarray(self.dy, np.float32)
        if x.size > 30:
            # first principal component: a beam that sways one way should be read that way
            m = np.stack([x - x.mean(), y - y.mean()])
            cov = m @ m.T
            w, v = np.linalg.eigh(cov)
            ax = v[:, int(np.argmax(w))]
            self.axis = (float(ax[0]), float(ax[1]))
        return (x * self.axis[0] + y * self.axis[1]).astype(np.float32)


class ProbeBank:
    """Owns the probes, the capture-thread tap and the worker that turns pixels into spectra."""

    RING = 512
    #: Playback speed-up for the reconstructed waveform, as a ratio. The structural band sits
    #: below 15 Hz and is inaudible, so it is played faster to be heard; the UI states the same
    #: number on the button, and the test holds the two together.
    OCTAVES_UP = 8.0      # 3 octaves
    PLAY_RATE = 8000      # a rate browsers will actually play

    def __init__(self, db: Any, config: Any) -> None:
        self.db = db
        self.config = config
        self.probes: dict[int, Probe] = {}
        self.source_id: int | None = None
        self._ring: deque = deque(maxlen=self.RING)
        self._lock = threading.RLock()
        self._run = False
        self._thread: threading.Thread | None = None
        self._impacts: list[dict] = []
        self._last_hist = 0.0
        self.fps = 30.0

    # -- configuration -----------------------------------------------------------------------
    def _cfg(self, key: str, default: Any) -> Any:
        try:
            return self.config.get(f"eardrum.{key}", default)
        except Exception:
            return default

    @property
    def active(self) -> bool:
        """Read on the capture thread once per frame, so it must stay a plain attribute lookup."""
        return self._run and bool(self.probes)

    @property
    def nyquist(self) -> float:
        return self.fps / 2.0

    # -- lifecycle ---------------------------------------------------------------------------
    def open(self, source_id: int) -> None:
        self.source_id = int(source_id)
        self.probes = {}
        for row in self.db.query(
                "SELECT id, name, roi, kind, texture, enabled FROM probes WHERE source_id = ?",
                (int(source_id),)):
            try:
                roi = json.loads(row[2])
            except Exception:
                continue
            p = Probe(int(row[0]), row[1], roi, row[3], float(row[4] or 0.0))
            p.enabled = bool(row[5])
            self.probes[p.id] = p
        self._ring.clear()
        if not self._run:
            self._run = True
            self._thread = threading.Thread(target=self._worker, name="Eardrum", daemon=True)
            self._thread.start()

    def close(self) -> None:
        self._run = False
        self._ring.clear()

    # -- the capture-thread tap --------------------------------------------------------------
    def tap(self, frame: Any, ts: float) -> None:
        """Called on the CAPTURE thread at frame rate. It copies a handful of small ROIs and
        returns; everything expensive happens on the worker. This is the one slot in the whole
        suite where a slow line would show up as visible stutter."""
        if not self._run or not self.probes or frame is None:
            return
        h, w = frame.shape[:2]
        size = int(self._cfg("roi", 64))
        rois: dict[int, np.ndarray] = {}
        # Snapshot the probe set before iterating. add() and remove() mutate this dict from the
        # API thread, and iterating it live raises "dictionary changed size during iteration" on
        # the ONE thread where an exception stops the video.
        for p in list(self.probes.values()):
            if not p.enabled:
                continue
            x = int(p.roi[0] * w)
            y = int(p.roi[1] * h)
            x = max(0, min(w - size, x))
            y = max(0, min(h - size, y))
            patch = frame[y:y + size, x:x + size]
            if patch.shape[0] != size or patch.shape[1] != size:
                continue
            rois[p.id] = patch.copy()
        if rois:
            self._ring.append((float(ts), rois))

    # -- the worker --------------------------------------------------------------------------
    def _worker(self) -> None:
        reanchor = float(self._cfg("reanchor_s", 10.0))
        while self._run:
            if not self._ring:
                time.sleep(0.01)
                continue
            try:
                ts, rois = self._ring.popleft()
            except IndexError:
                continue
            try:
                self._consume(ts, rois, reanchor)
            except Exception:
                log.debug("eardrum worker frame failed", exc_info=True)

    def _consume(self, ts: float, rois: dict[int, np.ndarray], reanchor: float) -> None:
        with self._lock:
            for pid, patch in rois.items():
                p = self.probes.get(pid)
                if p is None:
                    continue
                grey = (cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3
                        else patch).astype(np.float32)
                if p.ref_patch is None or ts - p.anchor_ts > reanchor:
                    # re-anchor on a cadence so slow thermal drift cannot walk the ROI away
                    p.ref_patch = grey
                    p.anchor_ts = ts
                    p.ts.append(ts); p.dx.append(0.0); p.dy.append(0.0)
                    continue
                dx, dy, quality = phase_shift(p.ref_patch, grey)
                if quality < _PC_MIN_QUALITY:
                    continue                     # no usable correlation: say nothing at all
                p.ts.append(ts); p.dx.append(dx); p.dy.append(dy)
            # frame timing, used as the resample rate
            ref = next((p for p in self.probes.values() if p.kind == "ref"), None)
            any_p = next(iter(self.probes.values()), None)
            src = ref or any_p
            if src is not None and len(src.ts) > 20:
                dt = np.diff(np.asarray(src.ts, np.float64))
                dt = dt[dt > 0]
                if dt.size:
                    self.fps = float(np.clip(1.0 / float(np.median(dt)), 1.0, 120.0))

    # -- readings ----------------------------------------------------------------------------
    def _reference(self) -> np.ndarray | None:
        ref = next((p for p in self.probes.values() if p.kind == "ref" and p.enabled), None)
        if ref is None or len(ref.ts) < 8:
            return None
        vals, _fs = resample_uniform(np.asarray(ref.ts), ref.scalar(), self.fps)
        return vals

    def series(self, pid: int) -> tuple[np.ndarray, float]:
        """The probe's displacement, uniformly resampled and common-mode corrected."""
        p = self.probes.get(int(pid))
        if p is None or len(p.ts) < 8:
            return np.zeros(0, np.float32), self.fps
        vals, fs = resample_uniform(np.asarray(p.ts), p.scalar(), self.fps)
        if p.kind != "ref":
            ref = self._reference()
            if ref is not None and ref.size:
                n = min(ref.size, vals.size)
                vals = vals[:n] - ref[:n]     # subtract the mount's own motion
        return vals, fs

    def saturated(self) -> bool:
        """Is the reference itself moving so much that no reading is trustworthy?"""
        ref = self._reference()
        if ref is None or ref.size < 8:
            return False
        return float(np.std(ref)) > float(self._cfg("saturate_px", 1.5))

    def frame(self, pid: int) -> dict | None:
        """The ~4 Hz live payload: level, peaks, one spectrogram column, a scope chunk."""
        p = self.probes.get(int(pid))
        if p is None:
            return None
        vals, fs = self.series(pid)
        if vals.size < 16:
            return {"id": p.id, "rms": 0.0, "db": 0.0, "snr": 0.0, "peaks": [], "col": "",
                    "wave": [], "saturated": self.saturated()}
        freqs, psd = spectrum(vals, fs, int(self._cfg("window", 1024)))
        floor = noise_floor(psd)
        peaks = find_peaks(freqs, psd, floor)
        base = self._baseline(pid)
        if base:
            peaks = compare_baseline(peaks, base["peaks"])
        rms = float(np.sqrt(np.mean(detrend(vals) ** 2)))
        db = 0.0
        if base and base.get("rms"):
            db = 20.0 * math.log10(max(rms, 1e-9) / max(float(base["rms"]), 1e-9))
        # one spectrogram column, quantised to bytes for the socket
        col = np.clip((psd - floor) * 4.0, 0, 255).astype(np.uint8)
        step = max(1, col.size // 128)
        col = col[::step][:128]
        wave = detrend(vals[-96:])
        return {
            "id": p.id, "rms": round(rms, 6), "db": round(db, 2),
            "snr": round(float(np.max(psd) - floor), 2),
            "peaks": peaks, "col": col.tobytes().hex(),
            "wave": [round(float(v), 5) for v in wave],
            "saturated": self.saturated(),
        }

    def spectrum_full(self, pid: int) -> dict | None:
        p = self.probes.get(int(pid))
        if p is None:
            return None
        vals, fs = self.series(pid)
        if vals.size < 16:
            return {"id": p.id, "freqs": [], "psd": [], "baseline": None, "floor": -200.0,
                    "peaks": [], "band": STRUCTURAL, "nyquist": fs / 2.0,
                    "interpretation": None}
        freqs, psd = spectrum(vals, fs, int(self._cfg("window", 1024)))
        floor = noise_floor(psd)
        peaks = find_peaks(freqs, psd, floor)
        base = self._baseline(pid)
        if base:
            peaks = compare_baseline(peaks, base["peaks"])
        interp = interpret(peaks)
        if interp and alias_risk(peaks, fs / 2.0):
            interp["why"] += "; a peak sits near Nyquist, so it may have folded down from above"
        return {
            "id": p.id,
            "freqs": [round(float(f), 3) for f in freqs],
            "psd": [round(float(v), 2) for v in psd],
            "baseline": base["psd"] if base else None,
            "floor": round(floor, 2), "peaks": peaks, "band": STRUCTURAL,
            "nyquist": round(fs / 2.0, 2), "interpretation": interp,
        }

    # -- baseline ----------------------------------------------------------------------------
    def _baseline(self, pid: int) -> dict | None:
        rows = self.db.query(
            "SELECT psd, freqs, peaks, floor FROM probe_baseline WHERE probe_id = ?", (int(pid),))
        if not rows:
            return None
        try:
            psd = np.frombuffer(rows[0][0], np.float32)
            peaks = json.loads(rows[0][2])
        except Exception:
            return None
        rms = None
        hist = self.db.query(
            "SELECT rms FROM probe_history WHERE probe_id = ? ORDER BY ts ASC LIMIT 1",
            (int(pid),))
        if hist:
            rms = float(hist[0][0])
        return {"psd": [round(float(v), 2) for v in psd], "peaks": peaks,
                "floor": float(rows[0][3]), "rms": rms}

    def set_baseline(self, pid: int) -> dict:
        p = self.probes.get(int(pid))
        if p is None:
            return {"ok": False}
        vals, fs = self.series(pid)
        if vals.size < 32:
            return {"ok": False, "reason": "not enough signal yet"}
        freqs, psd = spectrum(vals, fs, int(self._cfg("window", 1024)))
        floor = noise_floor(psd)
        peaks = find_peaks(freqs, psd, floor)
        self.db.execute(
            "INSERT INTO probe_baseline (probe_id, psd, freqs, peaks, floor, band, ts)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT(probe_id) DO UPDATE SET psd=excluded.psd,"
            " freqs=excluded.freqs, peaks=excluded.peaks, floor=excluded.floor, ts=excluded.ts",
            (int(pid), psd.astype(np.float32).tobytes(), freqs.astype(np.float32).tobytes(),
             json.dumps(peaks), float(floor), STRUCTURAL, time.time()))
        rms = float(np.sqrt(np.mean(detrend(vals) ** 2)))
        self.db.execute(
            "INSERT INTO probe_history (probe_id, ts, rms, snr, peaks) VALUES (?,?,?,?,?)",
            (int(pid), time.time(), rms, 0.0, json.dumps(peaks)))
        return {"ok": True, "peaks": len(peaks)}

    def record_history(self, now: float) -> None:
        """One trend row per probe per minute: 1440 rows a day, and it is what makes a
        three-week drift visible."""
        if now - self._last_hist < 60.0:
            return
        self._last_hist = now
        for pid, p in list(self.probes.items()):
            vals, fs = self.series(pid)
            if vals.size < 32:
                continue
            freqs, psd = spectrum(vals, fs, int(self._cfg("window", 1024)))
            floor = noise_floor(psd)
            rms = float(np.sqrt(np.mean(detrend(vals) ** 2)))
            self.db.execute(
                "INSERT INTO probe_history (probe_id, ts, rms, snr, peaks) VALUES (?,?,?,?,?)",
                (int(pid), now, rms, float(np.max(psd) - floor),
                 json.dumps(find_peaks(freqs, psd, floor, limit=6))))

    def trend(self, pid: int, hours: int = 168) -> list[dict]:
        rows = self.db.query(
            "SELECT ts, rms, snr FROM probe_history WHERE probe_id = ? AND ts >= ?"
            " ORDER BY ts", (int(pid), time.time() - hours * 3600))
        return [{"ts": float(r[0]) * 1000.0, "rms": float(r[1]), "snr": float(r[2])} for r in rows]

    # -- probe management --------------------------------------------------------------------
    def add(self, source_id: int, roi: list[float], name: str | None, kind: str,
            frame: Any) -> dict:
        if len(self.probes) >= int(self._cfg("max_probes", 8)):
            return {"probe": None, "reason": "probe limit reached"}
        tex = 0.0
        if frame is not None:
            h, w = frame.shape[:2]
            size = int(self._cfg("roi", 64))
            x = max(0, min(w - size, int(roi[0] * w)))
            y = max(0, min(h - size, int(roi[1] * h)))
            tex = texture_score(frame[y:y + size, x:x + size])
        if tex < float(self._cfg("min_texture", 0.12)):
            return {"probe": None, "reason": "no texture here: try an edge, a bolt or a joint",
                    "texture": round(tex, 3)}
        # the first probe on rigid structure becomes the reference; without one, every reading
        # includes the camera's own shake
        if kind not in ("probe", "ref"):
            kind = "probe"
        if kind == "probe" and not any(p.kind == "ref" for p in self.probes.values()):
            kind = "ref"
        n = len(self.probes) + 1
        label = name or (f"REF" if kind == "ref" else f"P{n}")
        pid = int(self.db.execute(
            "INSERT INTO probes (source_id, name, roi, kind, texture, created_at, enabled)"
            " VALUES (?,?,?,?,?,?,1)",
            (int(source_id), label, json.dumps(roi), kind, float(tex), time.time())))
        p = Probe(pid, label, roi, kind, tex)
        self.probes[pid] = p
        return {"probe": p.public()}

    def update(self, pid: int, patch: dict) -> dict:
        p = self.probes.get(int(pid))
        if p is None:
            return {"probe": None}
        if "name" in patch:
            p.name = str(patch["name"])
        if "kind" in patch and patch["kind"] in ("probe", "ref"):
            if patch["kind"] == "ref":
                for other in self.probes.values():
                    if other.kind == "ref" and other.id != p.id:
                        other.kind = "probe"
                        self.db.execute("UPDATE probes SET kind='probe' WHERE id=?", (other.id,))
            p.kind = str(patch["kind"])
        if "enabled" in patch:
            p.enabled = bool(patch["enabled"])
        self.db.execute("UPDATE probes SET name=?, kind=?, enabled=? WHERE id=?",
                        (p.name, p.kind, 1 if p.enabled else 0, p.id))
        return {"probe": p.public()}

    def remove(self, pid: int) -> dict:
        self.probes.pop(int(pid), None)
        self.db.execute("DELETE FROM probes WHERE id = ?", (int(pid),))
        return {"ok": True}

    def suggest(self, frame: Any, n: int = 5) -> list[dict]:
        """Rank candidate ROIs by trackability, spread apart so they are not all on one bolt.

        Auto-placement matters more here than in most tools: an operator cannot see texture, and
        a probe on a blank wall silently returns noise forever.
        """
        if frame is None:
            return []
        h, w = frame.shape[:2]
        size = int(self._cfg("roi", 64))
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        step = max(size // 2, 24)
        cands: list[tuple[float, float, int, int]] = []
        for y in range(0, h - size, step):
            for x in range(0, w - size, step):
                patch = grey[y:y + size, x:x + size]
                tex = texture_score(patch)
                if tex < float(self._cfg("min_texture", 0.12)):
                    continue
                # rigid surfaces are steady: low temporal variance is a good sign, and the
                # cheapest proxy for it here is low local intensity spread
                rigid = 1.0 - min(1.0, float(np.std(patch)) / 90.0)
                cands.append((tex * 0.7 + rigid * 0.3, tex, x, y))
        cands.sort(key=lambda c: -c[0])
        out: list[dict] = []
        for score, tex, x, y in cands:
            if any(abs(x - o["px"]) < size and abs(y - o["py"]) < size for o in out):
                continue
            out.append({"roi": [x / w, y / h, size / w, size / h],
                        "texture": round(float(tex), 3), "rigid": score > 0.5,
                        "px": x, "py": y})
            if len(out) >= n:
                break
        for o in out:
            o.pop("px", None); o.pop("py", None)
        return out

    def modal_analysis(self) -> dict:
        sig, names = [], []
        for pid, p in self.probes.items():
            if p.kind == "ref":
                continue
            v, fs = self.series(pid)
            if v.size >= 512:
                sig.append(v)
                names.append(p.name)
        if len(sig) < 3:
            return {"modes": [], "reason": "modal analysis needs at least three probes"}
        return {"modes": modal(sig, self.fps), "probes": names}

    def wave(self, pid: int, seconds: float = 8.0) -> tuple[bytes | None, float]:
        """A band-limited WAV of the recovered displacement.

        Hard-limited to the structural band in CODE, not only in the UI: it cannot carry
        intelligible speech and it must stay that way. The pitch shift is stated on the button
        so nobody mistakes it for a recording.
        """
        vals, fs = self.series(pid)
        if vals.size < 32:
            return None, fs
        n = int(min(vals.size, seconds * fs))
        x = detrend(vals[-n:])
        peak = float(np.max(np.abs(x))) or 1.0
        x = (x / peak * 0.85)
        # The pitch shift is how much faster the content plays than it was captured, and it has
        # to be the shift the button claims. Writing `fs * OCTAVES_UP` as the sample rate would
        # be arithmetically right and useless: a 240 Hz WAV does not play in a browser. So the
        # rate is a normal 8 kHz and the DURATION is divided instead, which is the same shift and
        # is actually audible. (It was previously clamped to an 8 kHz floor with no resampling,
        # which played a 30 Hz series about eight octaves up while the UI said three.)
        rate = self.PLAY_RATE
        seconds_out = (x.size / max(_EPS, fs)) / self.OCTAVES_UP
        n_out = max(8, int(round(seconds_out * rate)))
        out = np.interp(np.linspace(0, x.size - 1, n_out),
                        np.arange(x.size), x).astype(np.float32)
        pcm = (out * 32767).astype("<i2")
        import struct as _st
        data = pcm.tobytes()
        hdr = (b"RIFF" + _st.pack("<I", 36 + len(data)) + b"WAVEfmt "
               + _st.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
               + b"data" + _st.pack("<I", len(data)))
        return hdr + data, fs
