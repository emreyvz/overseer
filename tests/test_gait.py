# tests/test_gait.py
"""Gait / soft-biometric descriptor: two walks of the same body must match (high cosine) and two
different body shapes must not, the descriptor must be scale invariant, cadence must be recovered,
and it must degrade gracefully when the legs are occluded."""
import numpy as np

from server.gait import GaitTracker, gait_descriptor

# COCO-17 indices
NOSE, LSH, RSH, LEL, REL, LWR, RWR, LHIP, RHIP, LKN, RKN, LAN, RAN = 0, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16


def _walk(body: dict, *, n=30, fps=15.0, step_hz=1.8, scale=1.0, drop_legs=False, noise=0.4, seed=0):
    """Synthesize a COCO-17 walking sequence for a body defined by segment lengths (px, x torso)."""
    rng = np.random.default_rng(seed)
    L = body["torso"] * scale
    seq = []
    for f in range(n):
        t = f / fps
        theta = 2 * np.pi * step_hz * t
        legA = 0.35 * np.sin(theta)               # left leg swing angle (rad)
        legB = -0.35 * np.sin(theta)              # right leg opposite phase
        armA, armB = -0.25 * np.sin(theta), 0.25 * np.sin(theta)
        cx, y_sh = 200.0 + 3.0 * f, 100.0
        msh = np.array([cx, y_sh])
        mhip = np.array([cx, y_sh + L])
        kp = np.zeros((17, 2)); cf = np.full(17, 0.9)
        kp[NOSE] = msh + [0, -body["head"] * scale]
        kp[LSH] = msh + [-body["sh_w"] * scale / 2, 0]
        kp[RSH] = msh + [body["sh_w"] * scale / 2, 0]
        kp[LHIP] = mhip + [-body["hip_w"] * scale / 2, 0]
        kp[RHIP] = mhip + [body["hip_w"] * scale / 2, 0]
        kp[LEL] = kp[LSH] + [np.sin(armA) * body["upper_arm"] * scale, np.cos(armA) * body["upper_arm"] * scale]
        kp[REL] = kp[RSH] + [np.sin(armB) * body["upper_arm"] * scale, np.cos(armB) * body["upper_arm"] * scale]
        kp[LWR] = kp[LEL] + [np.sin(armA) * body["forearm"] * scale, np.cos(armA) * body["forearm"] * scale]
        kp[RWR] = kp[REL] + [np.sin(armB) * body["forearm"] * scale, np.cos(armB) * body["forearm"] * scale]
        for side, hip, kn, an, ang in ((0, LHIP, LKN, LAN, legA), (1, RHIP, RKN, RAN, legB)):
            kp[kn] = kp[hip] + [np.sin(ang) * body["thigh"] * scale, np.cos(ang) * body["thigh"] * scale]
            kp[an] = kp[kn] + [np.sin(ang) * body["shin"] * scale, np.cos(ang) * body["shin"] * scale]
        kp += rng.normal(0, noise, kp.shape)
        if drop_legs:
            cf[[LKN, RKN, LAN, RAN]] = 0.0
        seq.append({"kpts": kp, "conf": cf, "t": t})
    return seq


BROAD = {"torso": 100, "sh_w": 118, "hip_w": 74, "upper_arm": 82, "forearm": 78,
         "thigh": 92, "shin": 84, "head": 58}   # broad shoulders, short legs
LEAN = {"torso": 100, "sh_w": 80, "hip_w": 66, "upper_arm": 92, "forearm": 88,
        "thigh": 128, "shin": 120, "head": 50}  # narrow, long legs


def _cos(a, b) -> float:
    return float(np.dot(a, b))   # both already L2-normalized


def test_same_body_matches_and_different_bodies_separate() -> None:
    a1 = gait_descriptor(_walk(BROAD, seed=1))
    a2 = gait_descriptor(_walk(BROAD, seed=2))
    b1 = gait_descriptor(_walk(LEAN, seed=3))
    assert a1 and a2 and b1
    same = _cos(a1["vector"], a2["vector"])
    diff = _cos(a1["vector"], b1["vector"])
    assert same > 0.85            # two walks of the same body -> high similarity
    assert diff < 0.5             # a clearly different body shape -> low similarity
    assert same > diff + 0.4


def test_scale_invariance() -> None:
    near = gait_descriptor(_walk(BROAD, seed=1, scale=1.0))
    far = gait_descriptor(_walk(BROAD, seed=1, scale=0.45))   # same person, further from camera
    assert _cos(near["vector"], far["vector"]) > 0.95


def test_cadence_recovered() -> None:
    d = gait_descriptor(_walk(BROAD, seed=5, step_hz=2.0, n=45))
    assert d["cadence_hz"] is not None
    assert abs(d["cadence_hz"] - 2.0) < 0.6     # step frequency recovered within tolerance
    assert d["has_dynamics"] is True


def test_graceful_without_legs() -> None:
    d = gait_descriptor(_walk(BROAD, seed=1, drop_legs=True))
    assert d is not None                        # soft-biometrics still produced
    assert d["has_dynamics"] is False
    assert d["soft_bio"]["shoulder_w"] is not None
    assert d["soft_bio"]["thigh"] is None       # legs were occluded


def test_too_few_frames_returns_none() -> None:
    assert gait_descriptor(_walk(BROAD, n=5)) is None


def test_gait_tracker_associates_and_emits() -> None:
    gt = GaitTracker(min_frames=12)
    for s in _walk(BROAD, n=22):
        kp = s["kpts"]
        x1, y1 = kp.min(0); x2, y2 = kp.max(0)
        pose = {"bbox": (float(x1), float(y1), float(x2), float(y2)), "kpts": s["kpts"], "conf": s["conf"]}
        # a person detection box that overlaps the skeleton -> matched by IoU
        gt.update([("trk-1", (float(x1) - 6, float(y1) - 6, float(x2) + 6, float(y2) + 6))],
                  [pose], now=s["t"])
    d = gt.descriptor("trk-1")
    assert d is not None and d["frames"] >= 12
    assert gt.descriptor("never-seen") is None
