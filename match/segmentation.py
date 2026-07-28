"""Foreground masking so appearance reads from the subject, not the background.

A real backend (YOLO-seg) can be injected; when absent, a deterministic centred-ellipse
mask is used. The ellipse is a coarse prior — most of a tight person/vehicle box is the
subject in the middle and background in the corners — and it is fully reproducible."""
from __future__ import annotations

import numpy as np


class Segmenter:
    def __init__(self, backend: object | None = None) -> None:
        # backend: optional object exposing available() -> bool and
        # mask(crop, cls) -> np.ndarray[bool] | None
        self._backend = backend

    def mask(self, crop: np.ndarray, cls: str = "") -> tuple[np.ndarray, float]:
        """Return (boolean HxW mask, coverage fraction in [0,1])."""
        if crop is None or crop.size == 0:
            return (np.zeros((1, 1), dtype=bool), 0.0)
        if self._backend is not None:
            try:
                if self._backend.available():                       # type: ignore[attr-defined]
                    m = self._backend.mask(crop, cls)               # type: ignore[attr-defined]
                    if m is not None and np.asarray(m).shape[:2] == crop.shape[:2]:
                        mb = np.asarray(m).astype(bool)
                        cov = float(mb.mean())
                        if cov > 0.02:            # ignore an empty/degenerate mask
                            return (mb, cov)
            except Exception:  # noqa: BLE001 - never let segmentation break a search
                pass
        return self._ellipse_mask(crop.shape[:2])

    @staticmethod
    def _ellipse_mask(hw: tuple[int, int]) -> tuple[np.ndarray, float]:
        h, w = int(hw[0]), int(hw[1])
        yy, xx = np.ogrid[:h, :w]
        cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
        ry, rx = max(1.0, h * 0.48), max(1.0, w * 0.42)
        m = ((yy - cy) / ry) ** 2 + ((xx - cx) / rx) ** 2 <= 1.0
        return (m, float(m.mean()))
