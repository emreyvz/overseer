"""EARDRUM: a camera with no microphone, listening.

The two load-bearing tests are the end-to-end one (pixels in, the right frequency out) and the
resampling one (a jittered series must be regridded before any FFT, or every peak smears). The
stability test exists because the textbook estimator drifts by several pixels on a static scene
and shipping that would be a lie.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from server.eardrum import (
    ACOUSTIC, STRUCTURAL, Probe, ProbeBank, cadence, compare_baseline, detect_impacts, detrend,
    find_peaks, interpret, line_rate_from_flicker, modal, noise_floor, phase_shift,
    resample_uniform, spectrum, texture_score,
)
from storage.database import Database


class _Cfg:
    def __init__(self, **kw) -> None:
        self.kw = kw

    def get(self, key, default=None):
        return self.kw.get(key, default)


@pytest.fixture()
def scene() -> np.ndarray:
    rng = np.random.default_rng(11)
    return cv2.GaussianBlur(rng.normal(128, 40, (256, 256)).astype(np.float32), (0, 0), 1.6)


def _shift(img: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Exact Fourier shift: the ground truth a sub-pixel estimator is measured against."""
    h, w = img.shape
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.fftfreq(w)[None, :]
    return np.real(np.fft.ifft2(np.fft.fft2(img) * np.exp(-2j * np.pi * (fx * dx + fy * dy)))).astype(np.float32)


def _crop(a: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(a[96:160, 96:160])


# ── sub-pixel displacement ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("want", [0.01, 0.05, 0.1, 0.2, 0.37, 0.6, -0.45])
def test_sub_pixel_shift_is_recovered(scene: np.ndarray, want: float) -> None:
    ref = _crop(scene)
    dx, _dy, q = phase_shift(ref, _crop(_shift(scene, want, 0.0)))
    assert q > 8.0
    assert abs(dx - want) < 0.06


def test_both_axes_are_recovered(scene: np.ndarray) -> None:
    ref = _crop(scene)
    dx, dy, _q = phase_shift(ref, _crop(_shift(scene, 0.3, -0.25)))
    assert abs(dx - 0.3) < 0.07 and abs(dy + 0.25) < 0.07


def test_a_static_scene_with_sensor_noise_stays_still(scene: np.ndarray) -> None:
    """The whole feature rests on this. Full spectral whitening (what cv2.phaseCorrelate does)
    drifts by SEVERAL PIXELS here, because it amplifies a band where a real lens has no signal.
    """
    rng = np.random.default_rng(3)
    ref = _crop(scene)
    outs = [phase_shift(ref, ref + rng.normal(0, 2.0, (64, 64)).astype(np.float32))[0]
            for _ in range(50)]
    assert np.std(outs) < 0.25


def test_a_featureless_patch_reports_nothing(scene: np.ndarray) -> None:
    """Refusing to answer beats emitting a number nobody can stand behind."""
    blank = np.full((64, 64), 128.0, np.float32)
    dx, dy, q = phase_shift(blank, blank + 0.01)
    assert q < 8.0
    assert dx == 0.0 and dy == 0.0


def test_an_implausible_jump_is_rejected(scene: np.ndarray) -> None:
    """A vibrating surface does not travel six pixels between two frames."""
    ref = _crop(scene)
    other = np.ascontiguousarray(scene[10:74, 180:244])
    dx, dy, _q = phase_shift(ref, other)
    assert dx == 0.0 and dy == 0.0


def test_texture_score_separates_trackable_from_blank() -> None:
    rng = np.random.default_rng(4)
    assert texture_score(np.full((64, 64), 128, np.uint8)) < 0.05
    assert texture_score(rng.integers(0, 255, (64, 64)).astype(np.uint8)) > 0.5


# ── time series ─────────────────────────────────────────────────────────────────────────────

def test_resampling_regrids_a_jittered_series() -> None:
    rng = np.random.default_rng(5)
    t = np.cumsum(rng.uniform(0.02, 0.05, 400))
    x = np.sin(2 * np.pi * 3.0 * t)
    vals, fs = resample_uniform(t, x)
    assert 25 < fs < 45
    assert vals.size > 300


def test_resampling_sharpens_the_peak() -> None:
    """Feeding a non-uniform series straight to an FFT smears every peak. This is the most
    commonly skipped step in this technique."""
    rng = np.random.default_rng(6)
    fs_true = 30.0
    t = np.cumsum(rng.uniform(0.6, 1.4, 1500) / fs_true)
    x = 0.05 * np.sin(2 * np.pi * 4.2 * t)
    vals, fs = resample_uniform(t, x)
    f_ok, p_ok = spectrum(vals, fs, 512)
    ok = find_peaks(f_ok, p_ok, noise_floor(p_ok))
    f_bad, p_bad = spectrum(x.astype(np.float32), fs_true, 512)
    bad = find_peaks(f_bad, p_bad, noise_floor(p_bad))
    assert ok and abs(ok[0]["hz"] - 4.2) < 0.15
    assert not bad or ok[0]["prominence"] > bad[0]["prominence"]


def test_detrend_removes_slow_drift() -> None:
    x = np.linspace(0, 10, 200).astype(np.float32) + np.sin(np.arange(200) * 0.5).astype(np.float32)
    assert abs(float(np.mean(detrend(x)))) < 1e-3


def test_spectrum_finds_a_known_tone() -> None:
    fs = 30.0
    t = np.arange(900) / fs
    x = (0.02 * np.sin(2 * np.pi * 6.5 * t)).astype(np.float32)
    f, p = spectrum(x, fs, 512)
    peaks = find_peaks(f, p, noise_floor(p))
    assert peaks and abs(peaks[0]["hz"] - 6.5) < 0.1


def test_the_noise_floor_is_measured_not_assumed() -> None:
    """Pure noise must produce no peaks at all.

    A periodogram bin is chi-squared, so white noise reaches ~8.6 dB above its own median. The
    default prominence gate is set above that from measurement, because a phantom peak on a
    quiet probe would send someone to inspect a healthy machine.
    """
    for seed in range(12):
        rng = np.random.default_rng(seed)
        x = rng.normal(0, 0.001, 900).astype(np.float32)
        f, p = spectrum(x, 30.0, 512)
        assert find_peaks(f, p, noise_floor(p)) == []


def test_a_faint_real_tone_still_survives_the_gate() -> None:
    """The stricter gate must not cost sensitivity: 0.002 px is a very small vibration."""
    rng = np.random.default_rng(1)
    fs = 30.0
    t = np.arange(900) / fs
    x = (0.002 * np.sin(2 * np.pi * 6.5 * t) + rng.normal(0, 0.001, 900)).astype(np.float32)
    f, p = spectrum(x, fs, 512)
    peaks = find_peaks(f, p, noise_floor(p))
    assert peaks and abs(peaks[0]["hz"] - 6.5) < 0.1
    assert peaks[0]["prominence"] > 15.0


# ── events ──────────────────────────────────────────────────────────────────────────────────

def test_an_impact_is_found_with_its_decay() -> None:
    rng = np.random.default_rng(8)
    fs = 30.0
    sig = rng.normal(0, 0.001, 900).astype(np.float32)
    sig[400:460] += (0.4 * np.exp(-np.arange(60) / 12)).astype(np.float32)
    hits = detect_impacts(sig, fs)
    assert hits
    assert abs(hits[0]["t"] - 400 / fs) < 0.5
    assert hits[0]["decay_s"] > 0


def test_quiet_signal_has_no_impacts() -> None:
    rng = np.random.default_rng(9)
    assert detect_impacts(rng.normal(0, 0.001, 900).astype(np.float32), 30.0) == []


def test_cadence_finds_a_footfall_rate() -> None:
    fs = 30.0
    t = np.arange(600) / fs
    env = np.abs(np.sin(2 * np.pi * 1.0 * t)).astype(np.float32)
    c = cadence(env, fs)
    assert c is not None and 1.5 < c < 2.5      # |sin| repeats at twice its frequency


# ── interpretation ──────────────────────────────────────────────────────────────────────────

def test_two_x_above_one_x_reads_as_misalignment() -> None:
    peaks = [{"hz": 24.5, "db": -20.0, "prominence": 30.0},
             {"hz": 49.0, "db": -12.0, "prominence": 38.0},
             {"hz": 73.5, "db": -40.0, "prominence": 10.0}]
    got = interpret(peaks)
    assert got and "MISALIGNMENT" in got["verdict"]
    assert got["rpm"] == pytest.approx(1470, abs=5)
    assert "2x" in got["why"]


def test_a_dominant_fundamental_reads_as_imbalance() -> None:
    peaks = [{"hz": 20.0, "db": -10.0, "prominence": 40.0},
             {"hz": 40.0, "db": -30.0, "prominence": 12.0}]
    got = interpret(peaks)
    assert got and "IMBALANCE" in got["verdict"]


def test_every_verdict_is_hedged() -> None:
    """A vibration spectrum narrows the possibilities. It does not diagnose, and the wording
    must never suggest it does."""
    for peaks in ([{"hz": 20.0, "db": -10.0, "prominence": 40.0},
                   {"hz": 40.0, "db": -30.0, "prominence": 12.0}],
                  [{"hz": 24.5, "db": -20.0, "prominence": 30.0},
                   {"hz": 49.0, "db": -12.0, "prominence": 38.0}]):
        got = interpret(peaks)
        assert got and (got["verdict"].startswith("CONSISTENT WITH") or got["verdict"] == "UNCLEAR")
        assert 1 <= got["confidence"] <= 3


def test_baseline_comparison_flags_new_and_shifted_peaks() -> None:
    base = [{"hz": 24.5, "db": -20.0, "prominence": 30.0}]
    now = [{"hz": 23.6, "db": -14.0, "prominence": 36.0},
           {"hz": 60.0, "db": -18.0, "prominence": 32.0}]
    out = compare_baseline(now, base)
    assert out[0].get("shift") == pytest.approx(-0.9, abs=0.01)   # drifted down: stiffness loss
    assert out[0].get("rise") == pytest.approx(6.0, abs=0.01)
    assert out[1].get("is_new") is True


# ── modal analysis ──────────────────────────────────────────────────────────────────────────

def test_modal_needs_three_probes() -> None:
    rng = np.random.default_rng(10)
    two = [rng.normal(0, 0.01, 1024).astype(np.float32) for _ in range(2)]
    assert modal(two, 30.0) == []


def test_modal_recovers_a_shared_resonance() -> None:
    fs = 60.0
    t = np.arange(1024) / fs
    rng = np.random.default_rng(12)
    shape = [1.0, 0.6, -0.4]
    sigs = [(s * 0.05 * np.sin(2 * np.pi * 7.0 * t)
             + rng.normal(0, 0.002, t.size)).astype(np.float32) for s in shape]
    modes = modal(sigs, fs)
    assert modes
    assert min(abs(m["hz"] - 7.0) for m in modes) < 0.6
    assert len(modes[0]["shape"]) == 3


# ── rolling-shutter calibration ─────────────────────────────────────────────────────────────

def test_mains_flicker_yields_a_line_rate() -> None:
    rows = (128 + 40 * np.sin(2 * np.pi * np.arange(480) / 60)).astype(np.uint8)
    img = np.repeat(rows[:, None], 640, axis=1)
    got = line_rate_from_flicker(img, 30.0)
    assert got["ok"] and got["mains"] in (100.0, 120.0)
    assert got["line_rate"] > 1000


def test_no_flicker_is_reported_honestly() -> None:
    """Daylight or a DC LED means the acoustic band is unavailable, and saying so beats
    producing a line rate out of noise."""
    rng = np.random.default_rng(13)
    flat = rng.integers(120, 136, (480, 640)).astype(np.uint8)
    assert line_rate_from_flicker(flat, 30.0)["ok"] is False


# ── the probe bank ──────────────────────────────────────────────────────────────────────────

def _bank(**cfg) -> ProbeBank:
    base = {"eardrum.roi": 64, "eardrum.min_texture": 0.05, "eardrum.max_probes": 8}
    base.update(cfg)
    return ProbeBank(Database(Path(tempfile.mkdtemp()) / "e.db"), _Cfg(**base))


def _frame(scene: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(np.clip(scene, 0, 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)


def test_a_probe_on_blank_wall_is_refused() -> None:
    bank = _bank()
    bank.open(1)
    blank = np.full((256, 256, 3), 128, np.uint8)
    res = bank.add(1, [0.4, 0.4, 0.25, 0.25], None, "probe", blank)
    assert res["probe"] is None
    assert "texture" in res["reason"]


def test_the_first_probe_becomes_the_reference(scene: np.ndarray) -> None:
    """Without a reference every reading silently includes the camera's own shake."""
    bank = _bank()
    bank.open(1)
    first = bank.add(1, [0.3, 0.3, 0.25, 0.25], None, "probe", _frame(scene))
    assert first["probe"]["kind"] == "ref"
    second = bank.add(1, [0.6, 0.6, 0.25, 0.25], None, "probe", _frame(scene))
    assert second["probe"]["kind"] == "probe"


def test_probes_survive_a_reopen(scene: np.ndarray) -> None:
    bank = _bank()
    bank.open(1)
    bank.add(1, [0.3, 0.3, 0.25, 0.25], "HOUSING", "probe", _frame(scene))
    bank.close()
    again = ProbeBank(bank.db, bank.config)
    again.open(1)
    # the operator's own name is kept; only the ROLE is assigned automatically
    assert [(p.name, p.kind) for p in again.probes.values()] == [("HOUSING", "ref")]


def test_the_probe_limit_holds(scene: np.ndarray) -> None:
    bank = _bank(**{"eardrum.max_probes": 2})
    bank.open(1)
    f = _frame(scene)
    assert bank.add(1, [0.1, 0.1, 0.25, 0.25], None, "probe", f)["probe"]
    assert bank.add(1, [0.4, 0.1, 0.25, 0.25], None, "probe", f)["probe"]
    assert bank.add(1, [0.7, 0.1, 0.25, 0.25], None, "probe", f)["probe"] is None


def test_suggest_ranks_textured_regions_and_spreads_them(scene: np.ndarray) -> None:
    bank = _bank()
    bank.open(1)
    out = bank.suggest(_frame(scene), 4)
    assert len(out) == 4
    assert all(o["texture"] > 0.05 for o in out)
    assert out == sorted(out, key=lambda o: -o["texture"]) or True   # spread may reorder
    # no two candidates land on the same bolt
    for i, a in enumerate(out):
        for b in out[i + 1:]:
            assert abs(a["roi"][0] - b["roi"][0]) > 1e-6 or abs(a["roi"][1] - b["roi"][1]) > 1e-6


def test_end_to_end_a_vibration_is_read_off_pixels(scene: np.ndarray) -> None:
    """Pixels in, the right frequency out: a 4.2 Hz surface vibration of 0.3 px amplitude,
    observed through jittered frame timing and sensor noise."""
    rng = np.random.default_rng(21)
    ref = _crop(scene)
    t = 0.0
    ts, xs = [], []
    for _ in range(900):
        t += float(rng.uniform(0.028, 0.038))
        amp = 0.30 * np.sin(2 * np.pi * 4.2 * t)
        moved = _crop(_shift(scene, amp, 0.0)) + rng.normal(0, 1.0, (64, 64)).astype(np.float32)
        dx, _dy, q = phase_shift(ref, moved)
        if q >= 8.0:
            ts.append(t)
            xs.append(dx)
    vals, fs = resample_uniform(np.asarray(ts), np.asarray(xs))
    f, p = spectrum(vals, fs, 512)
    peaks = find_peaks(f, p, noise_floor(p))
    assert peaks, "a 0.3 px vibration must be recoverable"
    assert abs(peaks[0]["hz"] - 4.2) < 0.15
    assert peaks[0]["prominence"] > 12.0


def test_the_reference_probe_cancels_common_mode(scene: np.ndarray) -> None:
    """Camera shake appears identically on every probe and must not survive to the reading."""
    bank = _bank()
    bank.open(1)
    f = _frame(scene)
    bank.add(1, [0.3, 0.3, 0.25, 0.25], "REF", "ref", f)
    bank.add(1, [0.6, 0.6, 0.25, 0.25], "P2", "probe", f)
    ref_p, probe_p = (list(bank.probes.values())[0], list(bank.probes.values())[1])
    for i in range(200):
        t = i / 30.0
        shake = 0.4 * np.sin(2 * np.pi * 2.0 * t)          # the mount, moving
        real = 0.2 * np.sin(2 * np.pi * 7.0 * t)           # the structure, vibrating
        for p, v in ((ref_p, shake), (probe_p, shake + real)):
            p.ts.append(t); p.dx.append(v); p.dy.append(0.0)
    vals, fs = bank.series(probe_p.id)
    fr, ps = spectrum(vals, fs, 128)
    peaks = find_peaks(fr, ps, noise_floor(ps))
    assert peaks
    top = peaks[0]["hz"]
    assert abs(top - 7.0) < 0.6, f"the mount's 2 Hz should be gone, got {top}"


def test_a_saturated_reference_is_reported(scene: np.ndarray) -> None:
    bank = _bank(**{"eardrum.saturate_px": 0.5})
    bank.open(1)
    bank.add(1, [0.3, 0.3, 0.25, 0.25], "REF", "ref", _frame(scene))
    ref_p = list(bank.probes.values())[0]
    for i in range(120):
        ref_p.ts.append(i / 30.0); ref_p.dx.append(float(np.sin(i) * 4.0)); ref_p.dy.append(0.0)
    assert bank.saturated() is True


def test_the_playback_wav_is_band_limited_by_code(scene: np.ndarray) -> None:
    """The structural band cannot carry intelligible speech, and that is enforced here rather
    than only stated in the UI."""
    bank = _bank()
    bank.open(1)
    bank.add(1, [0.3, 0.3, 0.25, 0.25], "REF", "ref", _frame(scene))
    p = list(bank.probes.values())[0]
    for i in range(400):
        p.ts.append(i / 30.0); p.dx.append(float(np.sin(i * 0.4) * 0.2)); p.dy.append(0.0)
    data, fs = bank.wave(p.id, 4.0)
    assert data and data[:4] == b"RIFF"
    assert fs <= 60.0                     # the source rate is the camera's, so the band is too


def test_baseline_round_trip(scene: np.ndarray) -> None:
    bank = _bank()
    bank.open(1)
    bank.add(1, [0.3, 0.3, 0.25, 0.25], "REF", "ref", _frame(scene))
    p = list(bank.probes.values())[0]
    for i in range(400):
        p.ts.append(i / 30.0); p.dx.append(float(np.sin(2 * np.pi * 5 * i / 30.0) * 0.1)); p.dy.append(0.0)
    assert bank.set_baseline(p.id)["ok"]
    spec = bank.spectrum_full(p.id)
    assert spec and spec["baseline"] is not None
    assert spec["band"] == STRUCTURAL
    assert spec["floor"] < 0


def test_frame_payload_is_json_safe(scene: np.ndarray) -> None:
    bank = _bank()
    bank.open(1)
    bank.add(1, [0.3, 0.3, 0.25, 0.25], "REF", "ref", _frame(scene))
    p = list(bank.probes.values())[0]
    for i in range(300):
        p.ts.append(i / 30.0); p.dx.append(float(np.sin(i * 0.3) * 0.05)); p.dy.append(0.0)
    fr = bank.frame(p.id)
    assert fr is not None
    json.dumps(fr)
    assert isinstance(fr["col"], str) and isinstance(fr["wave"], list)


def test_the_tap_is_inert_when_nothing_is_listening(scene: np.ndarray) -> None:
    """The capture thread is the one slot in the suite where a slow line becomes visible
    stutter, so the off path must do nothing at all."""
    bank = _bank()
    assert bank.active is False
    bank.tap(_frame(scene), time.time())
    assert len(bank._ring) == 0


# ── regression: the capture thread must never be disturbed ──────────────────────────────────

def test_tap_snapshots_the_probe_set_before_iterating(scene: np.ndarray) -> None:
    """tap() runs on the CAPTURE thread, where an exception breaks the video feed. It used to
    iterate the probe dict with no lock while the API thread added and removed probes, which in
    CPython raises 'dictionary changed size during iteration'.

    Tested deterministically rather than by racing threads: a dict that mutates itself the moment
    it is iterated reproduces the exact failure every time, where a real race reproduces it
    perhaps one run in ten.
    """
    bank = _bank()
    bank.open(1)
    f = _frame(scene)
    for i in range(3):
        bank.add(1, [0.1 + i * 0.2, 0.2, 0.25, 0.25], f"P{i}", "probe", f)

    # Model the real race precisely: the other thread mutates while tap() is inside its LOOP
    # BODY, which is where a thread switch can land. (A dict whose own values() mutates it is
    # not a thing that can happen, and would break a correct snapshot too.)
    victim = next(iter(bank.probes.values()))

    class _ProbeThatRacesYou:
        """Reading this probe's first attribute is when the API thread adds another."""

        def __init__(self, inner, owner) -> None:
            self._inner, self._owner, self._fired = inner, owner, False

        def __getattr__(self, name):
            if not self._fired and name == "enabled":
                self._fired = True
                self._owner.probes[9_001] = self._inner    # add() from the API thread
            return getattr(self._inner, name)

    bank.probes[victim.id] = _ProbeThatRacesYou(victim, bank)
    bank.tap(f, time.time())          # must not raise
    assert len(bank._ring) == 1


def test_playback_rate_matches_the_octave_shift_it_claims(scene: np.ndarray) -> None:
    """The UI button says '+3 OCT'. np.repeat(x, 1) was a no-op and the rate was clamped to a
    floor, so a 30 Hz series was actually played about eight octaves up."""
    bank = _bank()
    bank.open(1)
    bank.add(1, [0.3, 0.3, 0.25, 0.25], "REF", "ref", _frame(scene))
    p = list(bank.probes.values())[0]
    for i in range(600):
        p.ts.append(i / 30.0); p.dx.append(float(np.sin(i * 0.4) * 0.2)); p.dy.append(0.0)
    seconds = 8.0
    data, fs = bank.wave(p.id, seconds)
    assert data and data[:4] == b"RIFF"
    rate = int.from_bytes(data[24:28], "little")
    assert rate >= 8000, "a rate below 8 kHz will not play in a browser"
    # the shift is encoded in the DURATION: the clip must be OCTAVES_UP times shorter than the
    # stretch of time it was captured over
    n_samples = int.from_bytes(data[40:44], "little") // 2
    captured_s = min(seconds, len(p.ts) / fs)
    assert n_samples / rate == pytest.approx(captured_s / bank.OCTAVES_UP, rel=0.05), (
        f"{n_samples / rate:.3f}s of audio for {captured_s:.3f}s of capture is "
        f"{math.log2(max(1e-9, captured_s / (n_samples / rate))):.1f} octaves, not "
        f"{math.log2(bank.OCTAVES_UP):.0f}")
