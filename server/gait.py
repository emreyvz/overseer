"""Gait + soft-biometric descriptor.

Appearance re-ID breaks when a person changes clothes or their face is never visible. Their body is
harder to change: limb proportions and the way they walk are stable, clothing-invariant identity
cues. This module turns a track's sequence of COCO-17 pose skeletons into one compact descriptor:

  soft biometrics  -- limb-length ratios normalized by torso length (a body-shape signature)
  gait dynamics    -- step cadence (Hz), stride amplitude, vertical bounce, arm swing

The descriptor is centered against rough population nominals and L2-normalized, so cosine compares
each person's deviation profile. Static ratios are always computed; gait dynamics are added when the
legs are visible across enough frames, else left neutral. Pure numpy, unit-testable from synthetic
skeletons.

COCO-17 indices: 0 nose, 5/6 shoulders, 7/8 elbows, 9/10 wrists, 11/12 hips, 13/14 knees, 15/16 ankles.
"""
from __future__ import annotations

from collections import deque

import numpy as np

_NOSE = 0
_L_SH, _R_SH = 5, 6
_L_EL, _R_EL = 7, 8
_L_WR, _R_WR = 9, 10
_L_HIP, _R_HIP = 11, 12
_L_KN, _R_KN = 13, 14
_L_AN, _R_AN = 15, 16
_KP_CONF = 0.4

# feature order: (name, nominal, spread) -- nominal/spread are rough population values used to turn a
# raw measurement into a z-like deviation so cosine compares body-shape PROFILES, not absolute scale.
_FEATURES: list[tuple[str, float, float]] = [
    ("shoulder_w", 0.95, 0.18),      # ratios below are relative to torso length (shoulder->hip)
    ("hip_w", 0.70, 0.15),
    ("upper_arm", 0.85, 0.15),
    ("forearm", 0.80, 0.15),
    ("thigh", 1.15, 0.20),
    ("shin", 1.10, 0.20),
    ("head", 0.55, 0.15),
    ("shoulder_hip", 1.35, 0.25),    # shoulder width / hip width (build)
    ("leg_torso", 2.25, 0.35),       # (thigh+shin) / torso
    ("cadence_hz", 1.80, 0.60),      # gait dynamics (0 => neutral when legs not visible)
    ("stride_amp", 0.45, 0.20),
    ("vbounce", 0.05, 0.04),
    ("arm_swing", 0.28, 0.15),
]
DIM = len(_FEATURES)


def _pt(kpts: np.ndarray, conf: np.ndarray, i: int) -> np.ndarray | None:
    return kpts[i] if conf[i] >= _KP_CONF else None


def _dist(a, b) -> float | None:
    if a is None or b is None:
        return None
    return float(np.hypot(a[0] - b[0], a[1] - b[1]))


def _mid(a, b):
    if a is None or b is None:
        return None
    return (a + b) / 2.0


def _body_ratios(kpts: np.ndarray, conf: np.ndarray) -> dict | None:
    """Per-frame limb ratios relative to torso length; None where an endpoint is not confident."""
    lsh, rsh = _pt(kpts, conf, _L_SH), _pt(kpts, conf, _R_SH)
    lhip, rhip = _pt(kpts, conf, _L_HIP), _pt(kpts, conf, _R_HIP)
    msh, mhip = _mid(lsh, rsh), _mid(lhip, rhip)
    torso = _dist(msh, mhip)
    if not torso or torso < 1.0:
        return None                                   # no reliable scale -> skip this frame
    def r(d):
        return d / torso if d is not None else None
    sh_w, hip_w = _dist(lsh, rsh), _dist(lhip, rhip)
    out = {
        "shoulder_w": r(sh_w),
        "hip_w": r(hip_w),
        "upper_arm": r(_avg(_dist(lsh, _pt(kpts, conf, _L_EL)), _dist(rsh, _pt(kpts, conf, _R_EL)))),
        "forearm": r(_avg(_dist(_pt(kpts, conf, _L_EL), _pt(kpts, conf, _L_WR)),
                          _dist(_pt(kpts, conf, _R_EL), _pt(kpts, conf, _R_WR)))),
        "thigh": r(_avg(_dist(lhip, _pt(kpts, conf, _L_KN)), _dist(rhip, _pt(kpts, conf, _R_KN)))),
        "shin": r(_avg(_dist(_pt(kpts, conf, _L_KN), _pt(kpts, conf, _L_AN)),
                       _dist(_pt(kpts, conf, _R_KN), _pt(kpts, conf, _R_AN)))),
        "head": r(_dist(_pt(kpts, conf, _NOSE), msh)),
        "shoulder_hip": (sh_w / hip_w) if (sh_w and hip_w) else None,
    }
    if out["thigh"] is not None and out["shin"] is not None:
        out["leg_torso"] = out["thigh"] + out["shin"]
    else:
        out["leg_torso"] = None
    return out


def _avg(a: float | None, b: float | None) -> float | None:
    vals = [x for x in (a, b) if x is not None]
    return sum(vals) / len(vals) if vals else None


def _median_ignore_none(vals: list) -> float | None:
    xs = [v for v in vals if v is not None and np.isfinite(v)]
    return float(np.median(xs)) if xs else None


def _cadence(sep: np.ndarray, ts: np.ndarray) -> float:
    """Step frequency (Hz) from the oscillation of the ankle horizontal separation via autocorrelation."""
    if sep.size < 8:
        return 0.0
    x = sep - sep.mean()
    if np.allclose(x, 0):
        return 0.0
    ac = np.correlate(x, x, mode="full")[x.size - 1:]
    if ac[0] <= 0:
        return 0.0
    ac = ac / ac[0]
    # first autocorrelation peak after the zero lag = one gait cycle
    peak = 0
    for i in range(2, ac.size - 1):
        if ac[i] > 0.3 and ac[i] > ac[i - 1] and ac[i] >= ac[i + 1]:
            peak = i
            break
    if peak == 0:
        return 0.0
    dur = float(ts[-1] - ts[0])
    if dur <= 1e-3:
        return 0.0
    fps = (ts.size - 1) / dur
    return float(fps / peak)   # cycles per second


def gait_descriptor(seq: list[dict], *, min_frames: int = 10) -> dict | None:
    """Build a soft-biometric + gait descriptor from a track's pose sequence.

    Args:
        seq: list of {"kpts": (17,2) float, "conf": (17,) float, "t": float seconds}.
        min_frames: minimum usable frames before a descriptor is emitted.

    Returns {vector (float32, L2-normed), dims, soft_bio, cadence_hz, frames, has_dynamics} or None.
    """
    if len(seq) < min_frames:
        return None
    ratios: dict[str, list] = {k: [] for k, _, _ in _FEATURES if k not in
                               ("cadence_hz", "stride_amp", "vbounce", "arm_swing")}
    ank_sep, hip_y, arm_off, ts = [], [], [], []
    used = 0
    for s in seq:
        kpts, conf = np.asarray(s["kpts"], float), np.asarray(s["conf"], float)
        if kpts.shape != (17, 2) or conf.shape != (17,):
            continue
        br = _body_ratios(kpts, conf)
        if br is None:
            continue
        used += 1
        for k in ratios:
            ratios[k].append(br.get(k))
        # gait-dynamics raw series (normalized by torso via ratios where possible)
        lsh, rsh = _pt(kpts, conf, _L_SH), _pt(kpts, conf, _R_SH)
        lhip, rhip = _pt(kpts, conf, _L_HIP), _pt(kpts, conf, _R_HIP)
        torso = _dist(_mid(lsh, rsh), _mid(lhip, rhip)) or 1.0
        lan, ran = _pt(kpts, conf, _L_AN), _pt(kpts, conf, _R_AN)
        if lan is not None and ran is not None:
            ank_sep.append(abs(lan[0] - ran[0]) / torso)
        mhip = _mid(lhip, rhip)
        if mhip is not None:
            hip_y.append(mhip[1] / torso)
        lwr, msh = _pt(kpts, conf, _L_WR), _mid(lsh, rsh)
        if lwr is not None and msh is not None:
            arm_off.append((lwr[0] - msh[0]) / torso)
        ts.append(float(s.get("t", used)))

    if used < min_frames:
        return None

    soft = {k: _median_ignore_none(v) for k, v in ratios.items()}
    tsa = np.asarray(ts, float)
    cadence = _cadence(np.asarray(ank_sep, float), tsa) if len(ank_sep) >= 8 else 0.0
    stride = float(np.percentile(ank_sep, 90)) if len(ank_sep) >= 4 else None
    vbounce = float(np.std(hip_y)) if len(hip_y) >= 4 else None
    arm_swing = float(np.std(arm_off)) if len(arm_off) >= 4 else None
    dyn = {"cadence_hz": cadence or None, "stride_amp": stride,
           "vbounce": vbounce, "arm_swing": arm_swing}

    vec = np.zeros(DIM, np.float32)
    allvals = {**soft, **dyn}
    for i, (name, nominal, spread) in enumerate(_FEATURES):
        val = allvals.get(name)
        if val is not None and np.isfinite(val):
            # z-like deviation, winsorized so one miscalibrated/outlier feature can't dominate the
            # descriptor and wash out the person-specific body-shape signal; missing stays neutral 0.
            vec[i] = float(np.clip((val - nominal) / spread, -2.5, 2.5))
    n = float(np.linalg.norm(vec))
    vec = vec / n if n > 1e-6 else vec

    return {
        "vector": vec.astype(np.float32),
        "dims": DIM,
        "soft_bio": {k: (round(v, 3) if v is not None else None) for k, v in soft.items()},
        "cadence_hz": round(cadence, 2) if cadence else None,
        "frames": used,
        "has_dynamics": cadence > 0 or (stride is not None),
    }


def _iou(a: tuple, b: tuple) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    ua = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1) + max(0.0, bx2 - bx1) * max(0.0, by2 - by1) - inter
    return inter / ua if ua > 1e-6 else 0.0


class GaitTracker:
    """Accumulate per-track pose skeletons over time and emit a gait descriptor when enough frames
    exist. Skeletons (from PoseKP.detect_pose) are matched to tracked person boxes by IoU, so each
    ByteTrack id builds its own walk sequence. In-memory only; stale tracks expire."""

    def __init__(self, *, maxlen: int = 48, min_frames: int = 14, iou_thr: float = 0.3,
                 ttl_s: float = 5.0) -> None:
        self.min_frames = min_frames
        self.iou_thr = iou_thr
        self.ttl_s = ttl_s
        self._buf: dict[str, deque] = {}
        self._last: dict[str, float] = {}
        self._desc: dict[str, dict] = {}
        self._desc_frames: dict[str, int] = {}
        self._maxlen = maxlen

    def update(self, persons: list[tuple[str, tuple]], poses: list[dict], now: float) -> None:
        """persons: [(track_key, bbox_px)]; poses: PoseKP.detect_pose output for the same frame."""
        used = set()
        for key, pbox in persons:
            best, bj = self.iou_thr, -1
            for j, pose in enumerate(poses):
                if j in used:
                    continue
                v = _iou(pbox, pose["bbox"])
                if v > best:
                    best, bj = v, j
            if bj < 0:
                continue
            used.add(bj)
            buf = self._buf.get(key)
            if buf is None:
                buf = self._buf[key] = deque(maxlen=self._maxlen)
            buf.append({"kpts": poses[bj]["kpts"], "conf": poses[bj]["conf"], "t": now})
            self._last[key] = now
        self._expire(now)

    def _expire(self, now: float) -> None:
        for key in [k for k, t in self._last.items() if now - t > self.ttl_s]:
            self._buf.pop(key, None)
            self._last.pop(key, None)
            self._desc.pop(key, None)
            self._desc_frames.pop(key, None)

    def descriptor(self, track_key: str) -> dict | None:
        """The track's gait descriptor, recomputed as it gathers more frames; cached between calls."""
        buf = self._buf.get(track_key)
        if buf is None or len(buf) < self.min_frames:
            return None
        if self._desc.get(track_key) is not None and len(buf) - self._desc_frames.get(track_key, 0) < 6:
            return self._desc[track_key]                     # reuse until 6 new frames accrue
        d = gait_descriptor(list(buf), min_frames=self.min_frames)
        if d is not None:
            self._desc[track_key] = d
            self._desc_frames[track_key] = len(buf)
        return d
