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


def _cap(img: np.ndarray, maxside: int) -> np.ndarray:
    """Downscale so the longest side <= maxside (keeps alignment fast; the upscale re-adds size)."""
    h, w = img.shape[:2]
    s = maxside / max(h, w)
    return img if s >= 1.0 else cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))),
                                           interpolation=cv2.INTER_AREA)


def _unsharp(img: np.ndarray, amount: float = 0.6, radius: float = 1.2) -> np.ndarray:
    blur = cv2.GaussianBlur(img, (0, 0), radius)
    return cv2.addWeighted(img, 1.0 + amount, blur, -amount, 0)


_FINAL_CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)) if cv2 is not None else None


def _finalize(img: np.ndarray) -> np.ndarray:
    """Strong-but-safe enhancement of the fused/selected image: edge-preserving denoise so sharpening
    does not amplify grain, local-contrast (CLAHE) on luma to reveal faint detail, then multi-scale
    unsharp (fine + coarse) so both fine texture and larger structure crisp up. Dark crops also get
    the app's low-light lift."""
    try:
        out = cv2.bilateralFilter(img, 5, 45, 45)                 # denoise, keep edges
        yuv = cv2.cvtColor(out, cv2.COLOR_BGR2YUV)
        yuv[:, :, 0] = _FINAL_CLAHE.apply(yuv[:, :, 0])
        out = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR)
        if float(out.mean()) < 90.0:
            from ai.yolo import _enhance_lowlight
            out = _enhance_lowlight(out, float(out.mean()))
        out = _unsharp(out, amount=0.75, radius=1.0)              # fine detail
        out = _unsharp(out, amount=0.35, radius=2.6)              # larger structure
        return out
    except Exception:  # noqa: BLE001
        return _unsharp(img)


def reconstruct(crops: list[np.ndarray], *, scale: float = 2.0, max_frames: int = 16,
                min_frames: int = 2, min_corr: float = 0.72, enhance: bool = True,
                sr=None) -> dict | None:
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

    # rank by sharpness, then cap the working resolution: ECC alignment runs on the small crops
    # (fast) and the resulting affine is scaled and applied to the upscaled frame — aligning at full
    # 2x resolution is what made this slow.
    CAP = 256
    ranked = [_cap(c, CAP) for c in
              sorted(valid, key=lambda c: (sharpness(c), c.shape[0] * c.shape[1]), reverse=True)[:max_frames]]
    ref = ranked[0]
    rh, rw = ref.shape[:2]
    ref_gray = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
    # align + fuse at the (low) working resolution; median rejects occluders/outliers and denoises.
    # Frames that align below min_corr are dropped, so disparate crops fall back to the reference and
    # the result is never worse than a plain zoom.
    stack: list[np.ndarray] = [ref.astype(np.float32)]
    crit = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 1e-4)
    for mov in ranked[1:]:
        mov_r = cv2.resize(mov, (rw, rh), interpolation=cv2.INTER_AREA) if mov.shape[:2] != (rh, rw) else mov
        warp = np.eye(2, 3, dtype=np.float32)
        try:
            cc, warp = cv2.findTransformECC(ref_gray, cv2.cvtColor(mov_r, cv2.COLOR_BGR2GRAY),
                                            warp, cv2.MOTION_AFFINE, crit, None, 5)
        except cv2.error:
            continue
        if not np.isfinite(cc) or float(cc) < min_corr or not np.all(np.isfinite(warp)):
            continue
        warped = cv2.warpAffine(mov_r, warp, (rw, rh),
                                flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REFLECT)
        stack.append(warped.astype(np.float32))
    fused_low = np.median(np.stack(stack, axis=0), axis=0).astype(np.uint8)

    # upscale: a learned super-resolution model (Real-ESRGAN) genuinely reconstructs detail; without
    # it, Lanczos + the classical finalize (denoise + CLAHE + multi-scale unsharp).
    used_sr = False
    out = None
    if enhance and sr is not None:
        try:
            if sr.available():
                out = sr.enhance(fused_low)
                used_sr = out is not None
        except Exception:  # noqa: BLE001
            out = None
    if out is None:
        out_w, out_h = max(16, int(round(rw * scale))), max(16, int(round(rh * scale)))
        out = cv2.resize(fused_low, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)
        if enhance:
            out = _finalize(out)

    return {
        "image": out,
        "method": "sr" if used_sr else ("multiframe" if len(stack) > 1 else "single"),
        "frames_used": len(stack),
        "frames_offered": len(valid),
    }
