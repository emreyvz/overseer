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


def encode_scene(rgb_grid: np.ndarray, disp01: np.ndarray, entities: list[dict], *,
                 fov: float, cam: str, sid: str, ts: float, jpeg_quality: int = 82) -> dict:
    """Assemble the wire payload: base64 JPEG for colour, base64 float32 for depth, plus the
    3D entity markers. `rgb_grid` is BGR (as OpenCV holds it); `disp01` is the matching grid."""
    h, w = disp01.shape
    ok, jpg = cv2.imencode(".jpg", rgb_grid, [cv2.IMWRITE_JPEG_QUALITY, int(jpeg_quality)])
    image_b64 = base64.b64encode(jpg.tobytes()).decode("ascii") if ok else ""
    depth_b64 = base64.b64encode(np.ascontiguousarray(disp01, np.float32).tobytes()).decode("ascii")
    return {
        "cam": cam, "sid": str(sid), "w": int(w), "h": int(h), "fov": float(fov),
        "image": image_b64, "depth": depth_b64, "entities": entities, "ts": float(ts),
    }
