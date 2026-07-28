"""A deterministic, download-free appearance encoder.

It is a coarse colour+structure signature (a downscaled, mean-centred BGR grid). It is
NOT identity-grade — its low ``trust`` caps the confidence of any match it produces, so
results are honestly labeled "baseline". Its jobs are: (1) make the whole pipeline and
its tests run offline and deterministically, and (2) degrade gracefully when a real ReID
model is unavailable instead of failing."""
from __future__ import annotations

import cv2
import numpy as np

from match.encoders.base import Encoder, apply_mask, l2_normalize


class DeterministicEncoder(Encoder):
    model_id = "baseline-grid-v1"
    trust = 0.35

    def __init__(self, grid: int = 16) -> None:
        self.grid = int(grid)
        self.dim = self.grid * self.grid * 3

    def available(self) -> bool:
        return True

    def _embed_one(self, crop: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
        if crop is None or crop.size == 0:
            return np.zeros(self.dim, dtype=np.float32)
        c = apply_mask(crop, mask)
        small = cv2.resize(c, (self.grid, self.grid),
                           interpolation=cv2.INTER_AREA).astype(np.float32)
        if small.ndim == 2:  # grayscale crop -> fake 3 channels so dim is stable
            small = np.repeat(small[:, :, None], 3, axis=2)
        v = small.reshape(-1)
        return v - float(v.mean())      # drop DC so brightness shifts don't dominate

    def encode(self, crops: list[np.ndarray],
               masks: list[np.ndarray | None] | None = None) -> np.ndarray:
        if not crops:
            return np.empty((0, self.dim), dtype=np.float32)
        rows = [self._embed_one(crop, masks[i] if masks else None)
                for i, crop in enumerate(crops)]
        return l2_normalize(np.stack(rows))
