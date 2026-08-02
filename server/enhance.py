"""Live 'Enhance': clarify a boxed region of a camera frame into a crisp, PHOTOGRAPHIC close-up.

A raw GAN super-resolution can look cartoonish (plasticky, invented texture). This instead upscales
the crop with high-quality Lanczos, applies the same edge-preserving denoise + local-contrast (CLAHE)
+ multi-scale unsharp used for photo reconstruction, and only LIGHTLY blends the learned SR for
genuine detail — so the result reads like a sharp optical zoom, not a cartoon.
"""
from __future__ import annotations

import base64


def enhance_region(frame, box, sr=None, scale: float = 4.0) -> str | None:
    """Return a `data:image/jpeg;base64,...` close-up of the normalized `box` (x, y, w, h in 0..1)
    of `frame`, or None. `sr` is an optional SuperResolver: when present we lean on it for genuine
    reconstructed detail (blended with a photographic Lanczos upscale so it stays natural, not
    cartoonish); without it we denoise + upscale + gently sharpen so it is clarified, not blocky."""
    try:
        import cv2
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
    # target resolution — generous so the loupe shows real detail (not a few upscaled blocks)
    th = int(min(1440, max(ch * scale, 360)))
    tw = max(1, int(cw * (th / ch)))
    # denoise first so upscaling doesn't amplify sensor grain / JPEG blocks
    base = cv2.bilateralFilter(crop, 7, 55, 55) if min(ch, cw) >= 10 else crop
    up = cv2.resize(base, (tw, th), interpolation=cv2.INTER_LANCZOS4)
    if sr is not None:
        try:
            if sr.available():
                # Reconstruct with the learned model up to (or past) the target, THEN area-downscale
                # to fit. A tiny crop reaches the loupe size through repeated 4x model passes, so the
                # detail is genuinely reconstructed instead of Lanczos-upscaled from a small SR output
                # (which is what made the old result look blocky/pixelated).
                cur = crop
                for _ in range(2):
                    if max(cur.shape[:2]) >= th:
                        break
                    nxt = sr.enhance(cur)
                    if nxt is None:
                        break
                    cur = nxt
                if cur is crop:                 # crop already large: one pass for extra detail
                    cur = sr.enhance(crop) or crop
                interp = cv2.INTER_AREA if cur.shape[0] > th else cv2.INTER_LANCZOS4
                srimg = cv2.resize(cur, (tw, th), interpolation=interp)
                up = cv2.addWeighted(srimg, 0.68, up, 0.32, 0)   # lean on reconstruction, keep natural texture
        except Exception:  # noqa: BLE001
            pass
    # gentle local contrast (reveals faint detail) — kept mild so it never looks stylised
    try:
        lab = cv2.cvtColor(up, cv2.COLOR_BGR2LAB)
        lab[:, :, 0] = cv2.createCLAHE(clipLimit=1.6, tileGridSize=(8, 8)).apply(lab[:, :, 0])
        up = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    except Exception:  # noqa: BLE001
        pass
    # gentle unsharp (single scale, low amount) — crisp without ringing/pixel artefacts
    try:
        blur = cv2.GaussianBlur(up, (0, 0), 1.4)
        up = cv2.addWeighted(up, 1.35, blur, -0.35, 0)
    except Exception:  # noqa: BLE001
        pass
    ok, buf = cv2.imencode(".jpg", up, [int(cv2.IMWRITE_JPEG_QUALITY), 94])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
