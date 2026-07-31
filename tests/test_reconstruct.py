# tests/test_reconstruct.py
"""Multi-frame reconstruction: a burst of noisy/shifted crops of the same thing must fuse into an
image that is measurably closer to the clean original than any single input frame, and the guards
must reject degenerate input."""
import cv2
import numpy as np

from server.reconstruct import reconstruct, sharpness


def _plate_like() -> np.ndarray:
    """A small, sharp BGR image of LARGE solid tiles + thick stripes: structured content that
    survives a 2x downscale (so the fusion target is well defined) yet has strong edges."""
    rng = np.random.default_rng(7)
    img = np.full((64, 120, 3), 30, np.uint8)
    for ty in range(0, 64, 16):            # 16x24 solid tiles (half-size 8x12 stays resolvable)
        for tx in range(0, 120, 24):
            img[ty:ty + 16, tx:tx + 24] = rng.integers(40, 230, size=3, dtype=np.uint8)
    img[:, ::12] = 240                     # thick vertical stripes -> edges
    return img


def _degrade(base: np.ndarray, *, shift: tuple[int, int], noise: float, rng) -> np.ndarray:
    h, w = base.shape[:2]
    M = np.float32([[1, 0, shift[0]], [0, 1, shift[1]]])
    moved = cv2.warpAffine(base, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    small = cv2.resize(moved, (w // 2, h // 2), interpolation=cv2.INTER_AREA)   # lose resolution
    small = cv2.resize(small, (w, h), interpolation=cv2.INTER_CUBIC)
    noisy = small.astype(np.float32) + rng.normal(0, noise, small.shape)
    return np.clip(noisy, 0, 255).astype(np.uint8)


def _mse(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean((a.astype(np.float32) - b.astype(np.float32)) ** 2))


def test_fusion_beats_single_frame() -> None:
    base = _plate_like()
    # the best any of these resolution-limited frames could be (noise removed, but detail already lost)
    deg = cv2.resize(cv2.resize(base, (60, 32), interpolation=cv2.INTER_AREA),
                     (120, 64), interpolation=cv2.INTER_CUBIC)
    rng = np.random.default_rng(1)
    frames = [np.clip(deg.astype(np.float32) + rng.normal(0, 22.0, deg.shape), 0, 255).astype(np.uint8)
              for _ in range(10)]

    # enhance=False isolates the fusion (the sharpening pass deliberately adds high-freq detail that
    # would otherwise make the fused image "further" from the smooth noise-free target)
    out = reconstruct(frames, scale=2.0, enhance=False)
    assert out is not None
    fused = out["image"]
    assert abs(fused.shape[1] - 240) <= 4 and abs(fused.shape[0] - 128) <= 4
    assert out["frames_used"] >= 2 and out["frames_offered"] == 10

    target = cv2.resize(deg, (fused.shape[1], fused.shape[0]), interpolation=cv2.INTER_LANCZOS4)
    single_up = cv2.resize(frames[0], (fused.shape[1], fused.shape[0]), interpolation=cv2.INTER_LANCZOS4)
    # fusing 10 noisy views lands closer to the noise-free image than any single noisy frame
    assert _mse(target, fused) < _mse(target, single_up)


def test_alignment_recovers_shifts() -> None:
    base = _plate_like()
    rng = np.random.default_rng(2)
    shifts = [(-2, 1), (1, -1), (2, 2), (-1, -2), (0, 1), (1, 0)]
    frames = [_degrade(base, shift=s, noise=14.0, rng=rng) for s in shifts]

    out = reconstruct(frames, scale=2.0, min_corr=0.4)
    assert out is not None
    # ECC should have aligned and accepted at least a couple of the shifted supporting frames
    assert out["frames_used"] >= 3


def test_sharpness_orders_blurred_below_sharp() -> None:
    base = _plate_like()
    blurred = cv2.GaussianBlur(base, (7, 7), 3)
    assert sharpness(base) > sharpness(blurred)


def test_guards_reject_degenerate_input() -> None:
    assert reconstruct([]) is None
    assert reconstruct([np.zeros((64, 120, 3), np.uint8)], min_frames=2) is None      # one frame
    tiny = [np.zeros((4, 4, 3), np.uint8) for _ in range(5)]
    assert reconstruct(tiny) is None                                                  # all too small
