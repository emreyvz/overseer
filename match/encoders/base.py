"""Encoder interface + shared helpers. Every encoder maps a batch of crops (with optional
foreground masks) to L2-normalized row vectors, deterministically."""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


def l2_normalize(mat: np.ndarray) -> np.ndarray:
    """Row-wise L2 normalization. Zero rows stay zero (their cosine is a -1.0 sentinel
    downstream) rather than dividing by zero."""
    mat = np.asarray(mat, dtype=np.float32)
    if mat.size == 0:
        return mat.reshape(mat.shape)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def apply_mask(crop: np.ndarray, mask: np.ndarray | None) -> np.ndarray:
    """Zero out background pixels so appearance is read from the subject, not the wall
    behind it. A shape-mismatched or missing mask is ignored."""
    if mask is None:
        return crop
    m = np.asarray(mask)
    if m.shape[:2] != crop.shape[:2]:
        return crop
    out = crop.copy()
    out[~m.astype(bool)] = 0
    return out


class Encoder(ABC):
    model_id: str = "encoder"
    trust: float = 0.5          # confidence ceiling for matches from this encoder, in (0,1]
    dim: int = 0

    @abstractmethod
    def available(self) -> bool:
        """True when the encoder can run (e.g. weights loaded)."""

    @abstractmethod
    def encode(self, crops: list[np.ndarray],
               masks: list[np.ndarray | None] | None = None) -> np.ndarray:
        """Return an (N, dim) float32 array of L2-normalized row vectors. Deterministic:
        the same crops (and masks) always produce the same vectors."""
