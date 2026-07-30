"""Spatial 3D scene payload assembly (Feature 4). Pure helpers — no model, no I/O — so the
depth→point-cloud contract is unit-testable in isolation. The Backend grabs the frame, runs
Depth Anything V2 and the detector, then delegates the numeric packaging here.

Depth Anything V2 emits relative inverse-depth (disparity): larger = nearer. We normalize to
[0,1] with 1 = nearest, and ship the RGB grid (base64 JPEG) plus the depth grid (base64
float32, row-major) so the browser can back-project through a pinhole model.
"""
from __future__ import annotations

import base64

import cv2
import numpy as np


def normalize_disparity(disp: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Return (disp01, dmin, dmax) with disp01 in [0,1], 1 = nearest."""
    dmin, dmax = float(disp.min()), float(disp.max())
    disp01 = ((disp - dmin) / (dmax - dmin + 1e-6)).astype(np.float32)
    return disp01, dmin, dmax


def entity_depth(disp: np.ndarray, box_xyxy: tuple[float, float, float, float],
                 frame_wh: tuple[int, int], dmin: float, dmax: float) -> float:
    """Median disparity inside a detection box (given in `frame_wh` pixel coords), normalized to
    [0,1] against the frame's own (dmin,dmax). Robust to the box being off the depth grid."""
    x1, y1, x2, y2 = box_xyxy
    fw, fh = frame_wh
    dh, dw = disp.shape
    bx1, by1 = int(x1 / fw * dw), int(y1 / fh * dh)
    bx2, by2 = int(x2 / fw * dw), int(y2 / fh * dh)
    patch = disp[max(0, by1):max(by1 + 1, by2), max(0, bx1):max(bx1 + 1, bx2)]
    dval = float(np.median(patch)) if patch.size else dmin
    return round((dval - dmin) / (dmax - dmin + 1e-6), 4)


def foreground_mask(disp01: np.ndarray, margin: float = 0.08) -> np.ndarray:
    """Mask (uint8 0/255) of 'standing object' pixels: disparity notably NEARER than the local
    background level. Estimate the background by a morphological opening (drops near spikes smaller
    than the kernel), then flag pixels that exceed it by `margin`. Lets the scene behind standing
    objects be inpainted even when no detector box exists (buildings, poles, foreground clutter)."""
    h, w = disp01.shape
    du8 = (np.clip(disp01, 0.0, 1.0) * 255.0).astype(np.uint8)
    k = max(9, ((min(h, w) // 10) | 1))   # odd kernel ~ a scene fraction
    bg = cv2.morphologyEx(du8, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    fg = ((du8.astype(np.int16) - bg.astype(np.int16)) > int(margin * 255)).astype(np.uint8) * 255
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))   # drop specks
    return cv2.dilate(fg, np.ones((3, 3), np.uint8), iterations=2)


def complete_background(rgb_grid: np.ndarray, disp01: np.ndarray,
                        boxes_norm: list[tuple[float, float, float, float]],
                        extra_mask: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Reconstruct the OCCLUDED background as real geometry. Where a foreground object hides the
    scene — a detected person/vehicle box, and/or the depth-derived `extra_mask` of standing
    objects — inpaint BOTH the depth and the texture from the surrounding background, so the
    wall/floor/scene continues behind it. The frontend renders this as a second mesh layer at its
    true depth — filling disocclusion holes with a plausible 3D surface (correct parallax), not a
    flat backdrop. Returns (bg_rgb_bgr, bg_disp01)."""
    h, w = disp01.shape
    mask = np.zeros((h, w), np.uint8)
    for (nx1, ny1, nx2, ny2) in boxes_norm:
        x1, y1 = int(nx1 * w), int(ny1 * h)
        x2, y2 = int(nx2 * w), int(ny2 * h)
        if x2 > x1 and y2 > y1:
            mask[max(0, y1):min(h, y2), max(0, x1):min(w, x2)] = 255
    if extra_mask is not None and extra_mask.shape == mask.shape:
        mask = cv2.bitwise_or(mask, extra_mask.astype(np.uint8))
    if not mask.any():
        return rgb_grid.copy(), disp01.copy()
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    rad = max(4, w // 60)
    bg_rgb = cv2.inpaint(rgb_grid, mask, rad, cv2.INPAINT_TELEA)
    du8 = (np.clip(disp01, 0.0, 1.0) * 255.0).astype(np.uint8)
    bg_du8 = cv2.inpaint(du8, mask, rad, cv2.INPAINT_TELEA)
    return bg_rgb, (bg_du8.astype(np.float32) / 255.0)


def encode_scene(rgb_grid: np.ndarray, disp01: np.ndarray, entities: list[dict], *,
                 fov: float, cam: str, sid: str, ts: float, jpeg_quality: int = 82,
                 bg_rgb: np.ndarray | None = None, bg_disp01: np.ndarray | None = None) -> dict:
    """Assemble the wire payload: base64 JPEG for colour, base64 float32 for depth, plus the
    3D entity markers. `rgb_grid` is BGR (as OpenCV holds it); `disp01` is the matching grid.
    When a completed background layer is supplied it's shipped too (bg_image / bg_depth)."""
    h, w = disp01.shape
    ok, jpg = cv2.imencode(".jpg", rgb_grid, [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)])
    image_b64 = base64.b64encode(jpg.tobytes()).decode("ascii") if ok else ""
    depth_b64 = base64.b64encode(np.ascontiguousarray(disp01, np.float32).tobytes()).decode("ascii")
    out = {
        "cam": cam, "sid": str(sid), "w": int(w), "h": int(h), "fov": float(fov),
        "image": image_b64, "depth": depth_b64, "entities": entities, "ts": float(ts),
    }
    if bg_rgb is not None and bg_disp01 is not None:
        ok2, jpg2 = cv2.imencode(".jpg", bg_rgb, [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)])
        out["bg_image"] = base64.b64encode(jpg2.tobytes()).decode("ascii") if ok2 else ""
        out["bg_depth"] = base64.b64encode(np.ascontiguousarray(bg_disp01, np.float32).tobytes()).decode("ascii")
    return out
