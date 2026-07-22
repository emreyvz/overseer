"""TorchScript ReID embedder and optional attribute (PAR) classifier."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def _preprocess(crops: list[np.ndarray], size: tuple[int, int]) -> np.ndarray:
    h, w = size
    batch = []
    for crop in crops:
        resized = cv2.resize(crop, (w, h))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        batch.append(rgb.transpose(2, 0, 1))
    return np.stack(batch)


class ReidEmbedder:
    def __init__(
        self,
        model_path: Path,
        device: str,
        input_size: tuple[int, int] = (256, 128),
    ) -> None:
        import torch

        self._torch = torch
        self._device = device
        self._size = input_size
        self._model = torch.jit.load(str(model_path), map_location=device).eval()

    def embed(self, crops: list[np.ndarray]) -> np.ndarray:
        if not crops:
            return np.empty((0, 0), dtype=np.float32)
        tensor = self._torch.from_numpy(_preprocess(crops, self._size)).to(
            self._device
        )
        with self._torch.no_grad():
            out = self._model(tensor)
        vecs = out.detach().cpu().numpy().astype(np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


class ModelAttributes:
    def __init__(
        self,
        model_path: Path,
        device: str,
        labels: list[str],
        input_size: tuple[int, int] = (256, 128),
    ) -> None:
        import torch

        self._torch = torch
        self._device = device
        self._labels = labels
        self._size = input_size
        self._model = torch.jit.load(str(model_path), map_location=device).eval()

    def classify(self, crops: list[np.ndarray]) -> list[str]:
        if not crops:
            return []
        tensor = self._torch.from_numpy(_preprocess(crops, self._size)).to(
            self._device
        )
        with self._torch.no_grad():
            logits = self._model(tensor)
        idx = logits.detach().cpu().numpy().argmax(axis=1)
        return [self._labels[int(i) % len(self._labels)] for i in idx]
