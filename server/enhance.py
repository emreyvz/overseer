"""Live 'Enhance': clarify a boxed region of a camera frame into a crisp, PHOTOGRAPHIC close-up.

A raw GAN super-resolution can look cartoonish (plasticky, invented texture). This instead upscales
the crop with high-quality Lanczos, applies the same edge-preserving denoise + local-contrast (CLAHE)
+ multi-scale unsharp used for photo reconstruction, and only LIGHTLY blends the learned SR for
genuine detail — so the result reads like a sharp optical zoom, not a cartoon.
"""
from __future__ import annotations

import base64


def enhance_region(frame, box, sr=None, scale: float = 3.0) -> str | None:
    """Return a `data:image/jpeg;base64,...` close-up of the normalized `box` (x, y, w, h in 0..1)
    of `frame`, or None. `sr` is an optional SuperResolver (blended subtly)."""
    try:
        import cv2
        from server.reconstruct import _finalize   # photographic finisher (denoise + CLAHE + unsharp)
    except Exception:  # noqa: BLE001
        return None
    if frame is None or getattr(frame, "size", 0) == 0:
        return None
    h, w = frame.shape[:2]
    try:
        x, y, bw, bh = (float(v) for v in box)
    except Exception:  # noqa: BLE001
        return None
    x0 = int(max(0.0, min(1.0, x)) * w)
    y0 = int(max(0.0, min(1.0, y)) * h)
    x1 = int(max(0.0, min(1.0, x + bw)) * w)
    y1 = int(max(0.0, min(1.0, y + bh)) * h)
    if x1 - x0 < 6 or y1 - y0 < 6:
        return None
    crop = frame[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    th = int(min(760, max(ch * scale, 180)))          # upscale, capped for a snappy round-trip
    tw = max(1, int(cw * (th / ch)))
    up = cv2.resize(crop, (tw, th), interpolation=cv2.INTER_LANCZOS4)
    if sr is not None:
        try:
            if sr.available():
                srimg = sr.enhance(crop)
                if srimg is not None:
                    srimg = cv2.resize(srimg, (tw, th), interpolation=cv2.INTER_AREA)
                    up = cv2.addWeighted(up, 0.7, srimg, 0.3, 0)   # subtle — keep it photographic
        except Exception:  # noqa: BLE001
            pass
    try:
        out = _finalize(up)
    except Exception:  # noqa: BLE001
        out = up
    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
