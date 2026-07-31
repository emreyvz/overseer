"""Human mesh recovery for the spatial 3D view (idea 2: per-class real 3D models).

A monocular depth map only sees a person's front, so they read as a flat cutout. ROMP (Monocular,
One-stage, Regression of Multiple 3D People) regresses a full posed SMPL BODY per detected person
in one feed-forward pass — a real 3D human (front, back, sides), not a hallucination. We ship each
body's vertices so the frontend can place a true 3D person into the scene instead of a relief.

Lazy singleton, thread-safe (shared GPU), degrades to None so the view just skips the bodies.
Needs the one-time SMPL setup in ~/.romp (SMPL_NEUTRAL.pth); if absent, load fails -> None.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

log = logging.getLogger("overseer.human")


class HumanMeshEstimator:
    """ROMP wrapper. `estimate(bgr)` -> dict with per-person SMPL bodies, or None.

    Returns {"verts": (P,6890,3) float32 root-relative metres, "trans": (P,3) metric camera-space
    translation, "center2d": (P,2) normalized image position of the body, "faces": (F,3) int shared
    SMPL topology}. Add trans to verts for camera-space; the frontend re-places each body onto the
    scene's own depth at center2d and scales it to fit."""

    def __init__(self, device: str | None = None) -> None:
        self._device = device
        self._lock = threading.Lock()
        self._model: Any = None
        self._faces: np.ndarray | None = None
        self._loaded = False
        self._failed = False

    @property
    def available(self) -> bool:
        return not self._failed

    def _ensure(self) -> bool:
        if self._loaded:
            return True
        if self._failed:
            return False
        try:
            import torch
            import romp
            settings = romp.main.default_settings
            for k, v in [("mode", "image"), ("render_mesh", False), ("show", False),
                         ("save_video", False), ("temporal_optimize", False), ("onnx", False)]:
                if hasattr(settings, k):
                    setattr(settings, k, v)
            if hasattr(settings, "GPU"):
                settings.GPU = 0 if torch.cuda.is_available() else -1
            self._model = romp.ROMP(settings)
            # SMPL face topology (fixed) from the converted model, shipped once for rendering.
            import os
            pth = os.path.join(os.path.expanduser("~"), ".romp", "SMPL_NEUTRAL.pth")
            d = torch.load(pth, weights_only=False, map_location="cpu")
            self._faces = np.ascontiguousarray(np.asarray(d["f"], dtype=np.int32))
            self._loaded = True
            log.info("human mesh model ready (ROMP)")
            return True
        except Exception:  # noqa: BLE001 - any failure (no SMPL, no romp) => feature off, never fatal
            log.exception("human mesh model load failed — 3D bodies disabled")
            self._failed = True
            return False

    def estimate(self, bgr: Any, boxes: Any = None) -> dict | None:
        """If `boxes` (normalized person xyxy) are given, run ROMP on each CROP (padded + upscaled)
        so small/distant surveillance people are large enough for the mesh regressor to recover them
        — ROMP misses tiny whole-frame people. Body placement uses the box; verts are root-relative
        so they're crop-independent. Without boxes, runs ROMP once on the whole frame."""
        if bgr is None:
            return None
        with self._lock:
            if not self._ensure():
                return None
            try:
                import cv2
                if boxes:
                    h, w = bgr.shape[:2]
                    vlist, c2d, s2d = [], [], []
                    for (nx1, ny1, nx2, ny2) in boxes:
                        x1, y1, x2, y2 = int(nx1 * w), int(ny1 * h), int(nx2 * w), int(ny2 * h)
                        pw, ph = int(0.2 * (x2 - x1)), int(0.15 * (y2 - y1))
                        cx1, cy1 = max(0, x1 - pw), max(0, y1 - ph)
                        cx2, cy2 = min(w, x2 + pw), min(h, y2 + ph)
                        if cx2 - cx1 < 8 or cy2 - cy1 < 8:
                            continue
                        crop = bgr[cy1:cy2, cx1:cx2]
                        sc = max(1.0, 256.0 / min(crop.shape[:2]))   # upscale so ROMP can see it
                        if sc > 1.0:
                            crop = cv2.resize(crop, (int(crop.shape[1] * sc), int(crop.shape[0] * sc)))
                        out = self._model(crop)
                        if out is None or "verts" not in out:
                            continue
                        v = np.asarray(out["verts"], dtype=np.float32)
                        if v.ndim != 3 or v.shape[0] == 0:
                            continue
                        vlist.append(np.ascontiguousarray(v[0]))     # the (largest) body in the crop
                        c2d.append([(nx1 + nx2) / 2, (ny1 + ny2) / 2])
                        s2d.append([nx2 - nx1, ny2 - ny1])
                    if not vlist:
                        return None
                    return {"verts": np.stack(vlist),
                            "trans": np.zeros((len(vlist), 3), np.float32),
                            "center2d": np.asarray(c2d, np.float32),
                            "size2d": np.asarray(s2d, np.float32), "faces": self._faces}
                out = self._model(bgr)
                if out is None or "verts" not in out:
                    return None
                verts = np.ascontiguousarray(np.asarray(out["verts"], dtype=np.float32))  # (P,6890,3)
                if verts.ndim != 3 or verts.shape[0] == 0:
                    return None
                trans = np.ascontiguousarray(np.asarray(out["cam_trans"], dtype=np.float32))  # (P,3)
                h, w = bgr.shape[:2]
                pj = np.asarray(out.get("pj2d_org"))   # (P, J, 2) in pixels
                p = verts.shape[0]
                if pj is not None and pj.ndim == 3:
                    x1, x2 = pj[:, :, 0].min(1), pj[:, :, 0].max(1)
                    y1, y2 = pj[:, :, 1].min(1), pj[:, :, 1].max(1)
                    center2d = np.stack([(x1 + x2) / 2 / w, (y1 + y2) / 2 / h], axis=1).astype(np.float32)
                    size2d = np.stack([(x2 - x1) / w, (y2 - y1) / h], axis=1).astype(np.float32)
                else:
                    center2d = np.full((p, 2), 0.5, np.float32)
                    size2d = np.full((p, 2), 0.2, np.float32)
                return {"verts": verts, "trans": trans, "center2d": center2d,
                        "size2d": size2d, "faces": self._faces}
            except Exception:  # noqa: BLE001
                log.exception("human mesh inference failed")
                return None
