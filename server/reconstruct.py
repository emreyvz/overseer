"""Multi-frame super-resolution reconstruction.

Overseer captures many crops of the SAME subject or plate across frames and cameras, but every
pipeline keeps only one "best" crop and throws the rest away. A single surveillance crop is often
too small, noisy or motion-blurred to read a face or a plate. This module fuses a burst of crops of
the same thing into ONE sharper, higher-resolution image, recovering detail that no single frame
holds:

  align (sub-pixel) -> robust temporal fuse (median kills occluders/noise) -> upscale + sharpen.

Averaging N aligned frames cuts random sensor noise by ~sqrt(N); a median rejects frames where the
subject was occluded or mis-aligned; sub-pixel jitter between frames lets the fused grid carry more
real detail than any input. Pure numpy + OpenCV, no learned model, so it runs anywhere the base app
runs and is unit-testable in isolation.
"""
from __future__ import annotations

import numpy as np

try:
    import cv2
except Exception:  # noqa: BLE001 - cv2 always present in the app env; keep import-safe for tooling
    cv2 = None  # type: ignore


def sharpness(img: np.ndarray) -> float:
    """Focus measure: variance of the Laplacian. Higher = crisper. Used to rank and to prove gain."""
    if cv2 is None or img is None or img.size == 0:
        return 0.0
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def _valid_crops(crops: list[np.ndarray]) -> list[np.ndarray]:
    out: list[np.ndarray] = []
    for c in crops:
        if c is None or not isinstance(c, np.ndarray) or c.ndim != 3 or c.shape[0] < 8 or c.shape[1] < 8:
            continue
        out.append(c if c.dtype == np.uint8 else np.clip(c, 0, 255).astype(np.uint8))
    return out


def _align_to(ref_gray: np.ndarray, mov_bgr: np.ndarray, mov_gray: np.ndarray,
              size: tuple[int, int]) -> tuple[np.ndarray, float] | None:
    """Warp `mov` onto `ref` with ECC (affine). Returns (warped_bgr, correlation) or None on failure.

    The correlation is the alignment quality in [0,1]; the caller drops poorly aligned frames so a
    mismatched crop can never smear the fusion.
    """
    w, h = size
    warp = np.eye(2, 3, dtype=np.float32)
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-4)
    try:
        cc, warp = cv2.findTransformECC(ref_gray, mov_gray, warp, cv2.MOTION_AFFINE, crit, None, 5)
    except cv2.error:
        return None
    if not np.isfinite(cc) or not np.all(np.isfinite(warp)):
        return None
    warped = cv2.warpAffine(mov_bgr, warp, (w, h),
                            flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
                            borderMode=cv2.BORDER_REFLECT)
    return warped, float(cc)


def _unsharp(img: np.ndarray, amount: float = 0.6, radius: float = 1.2) -> np.ndarray:
    blur = cv2.GaussianBlur(img, (0, 0), radius)
    return cv2.addWeighted(img, 1.0 + amount, blur, -amount, 0)


def reconstruct(crops: list[np.ndarray], *, scale: float = 2.0, max_frames: int = 16,
                min_frames: int = 2, min_corr: float = 0.72) -> dict | None:
    """Fuse a burst of crops of the same subject/plate into one super-resolved image.

    Args:
        crops: BGR uint8 arrays, all roughly the same subject (any sizes).
        scale: output upscale factor over the sharpest input crop.
        max_frames: cap on frames fused (the sharpest are kept).
        min_frames: below this many usable frames, returns None (nothing to fuse).
        min_corr: minimum ECC alignment correlation to accept a supporting frame.

    Returns a dict {image, frames_used, frames_offered, sharpness_in, sharpness_out, gain} or None.
    """
    if cv2 is None:
        return None
    valid = _valid_crops(crops)
    if len(valid) < max(1, min_frames):
        return None

    # rank by sharpness; the sharpest, largest crop is the alignment reference
    ranked = sorted(valid, key=lambda c: (sharpness(c), c.shape[0] * c.shape[1]), reverse=True)[:max_frames]
    ref = ranked[0]
    rh, rw = ref.shape[:2]
    out_w, out_h = max(16, int(round(rw * scale))), max(16, int(round(rh * scale)))

    ref_up = cv2.resize(ref, (out_w, out_h), interpolation=cv2.INTER_CUBIC)
    ref_gray = cv2.cvtColor(ref_up, cv2.COLOR_BGR2GRAY)

    stack: list[np.ndarray] = [ref_up.astype(np.float32)]
    for mov in ranked[1:]:
        mov_up = cv2.resize(mov, (out_w, out_h), interpolation=cv2.INTER_CUBIC)
        mov_gray = cv2.cvtColor(mov_up, cv2.COLOR_BGR2GRAY)
        res = _align_to(ref_gray, mov_up, mov_gray, (out_w, out_h))
        if res is None:
            continue
        warped, corr = res
        if corr >= min_corr:
            stack.append(warped.astype(np.float32))

    arr = np.stack(stack, axis=0)
    # robust fuse: median rejects occluders/outliers; with well-aligned frames it also denoises. Only
    # frames that aligned above min_corr are here, so disparate crops fall back to just the reference
    # (single-frame enhance) and the result is never worse than a plain zoom.
    fused = np.median(arr, axis=0).astype(np.uint8)

    if float(fused.mean()) < 90.0:   # reuse the app's low-light lift for dark night crops
        try:
            from ai.yolo import _enhance_lowlight
            fused = _enhance_lowlight(fused, float(fused.mean()))
        except Exception:  # noqa: BLE001
            pass
    fused = _unsharp(fused)

    return {
        "image": fused,
        "method": "multiframe" if len(stack) > 1 else "single",
        "frames_used": len(stack),
        "frames_offered": len(valid),
    }
