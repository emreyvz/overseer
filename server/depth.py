"""Monocular depth estimation for the spatial 3D scene view (Feature 4).

Wraps Depth Anything V2 (Small) — a DINOv2-backbone depth transformer — via `transformers`.
A single RGB frame yields a dense relative-depth (disparity) map with no stereo, LiDAR or
calibration. Runs on the existing CUDA torch stack in ~30 ms.

Design notes:
  * Lazy singleton — the model (and the `transformers` import) load on first use only, so a
    system that never opens the spatial view pays nothing.
  * Thread-safe — a lock serializes inference because the GPU is shared with the roster
    harvester / live detector; two large models racing on one device is asking for trouble.
  * Degrades, never crashes — if `transformers` is absent, the weights can't be fetched, or CUDA
    OOMs, `estimate()` returns None and the endpoint reports the view as unavailable.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

log = logging.getLogger("overseer.depth")


class DepthEstimator:
    """Depth Anything V2 wrapper. `estimate(bgr)` -> float32 disparity map (H×W), or None."""

    def __init__(self, model_name: str = "depth-anything/Depth-Anything-V2-Small-hf",
                 device: str | None = None) -> None:
        self.model_name = model_name
        self._device = device
        self._lock = threading.Lock()
        self._model: Any = None
        self._proc: Any = None
        self._loaded = False
        self._failed = False

    @property
    def available(self) -> bool:
        """True unless a previous load attempt failed hard (missing dep / no weights)."""
        return not self._failed

    def _ensure(self) -> bool:
        if self._loaded:
            return True
        if self._failed:
            return False
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModelForDepthEstimation
            dev = self._device or ("cuda" if torch.cuda.is_available() else "cpu")
            self._proc = AutoImageProcessor.from_pretrained(self.model_name)
            self._model = AutoModelForDepthEstimation.from_pretrained(self.model_name)
            self._model = self._model.to(dev).eval()
            self._device = dev
            self._loaded = True
            log.info("depth model ready: %s on %s", self.model_name, dev)
            return True
        except Exception:  # noqa: BLE001 - any failure => feature unavailable, never fatal
            log.exception("depth model load failed — spatial view disabled")
            self._failed = True
            return False

    def estimate(self, bgr: Any) -> np.ndarray | None:
        """Relative inverse-depth (disparity) for a BGR frame, upsampled to its own size.
        Larger values are NEARER (Depth Anything convention). None if unavailable."""
        if bgr is None:
            return None
        with self._lock:
            if not self._ensure():
                return None
            try:
                import torch
                # contiguous copy: the processor calls torch.from_numpy, which rejects the
                # negative stride a `[:, :, ::-1]` view would produce.
                rgb = np.ascontiguousarray(bgr[:, :, ::-1])  # BGR -> RGB
                h, w = rgb.shape[:2]
                with torch.no_grad():
                    inp = self._proc(images=rgb, return_tensors="pt").to(self._device)
                    pred = self._model(**inp).predicted_depth  # (1, h', w')
                    pred = torch.nn.functional.interpolate(
                        pred[:, None], size=(h, w), mode="bilinear", align_corners=False)
                depth = pred[0, 0].float().cpu().numpy()
                return np.ascontiguousarray(depth, dtype=np.float32)
            except Exception:  # noqa: BLE001
                log.exception("depth inference failed")
                return None
