"""Motion heatmap: decaying accumulator of motion masks, colorized overlay/export."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from core.config import Config


class MotionHeatmap:
    def __init__(self, config: Config) -> None:
        self._decay = float(config.get("heatmap.decay", 0.98))
        self._width = int(config.get("heatmap.width", 240))
        self._alpha = float(config.get("heatmap.alpha", 0.5))
        self._acc: np.ndarray | None = None

    @property
    def has_data(self) -> bool:
        return self._acc is not None and bool(np.any(self._acc > 1e-6))

    def _resize_to_acc(self, mask: np.ndarray) -> np.ndarray:
        """Resize binary/gray motion mask to width and proportional height."""
        height = max(1, int(self._width * mask.shape[0] / mask.shape[1]))
        small = cv2.resize(mask, (self._width, height))
        return small.astype(np.float32) / 255.0

    def accumulate(self, mask: np.ndarray) -> None:
        """Add normalized mask to accumulator: acc = acc*decay + mask_norm."""
        norm = self._resize_to_acc(mask)
        if self._acc is None or self._acc.shape != norm.shape:
            self._acc = norm.copy()
        else:
            # Always rebind to new array; never in-place mutation
            self._acc = self._acc * self._decay + norm

    def _colorized(self, size: tuple[int, int]) -> np.ndarray:
        """Normalize accumulator to [0,1], apply COLORMAP_JET, resize to target size."""
        acc = self._acc if self._acc is not None else np.zeros((1, 1), dtype=np.float32)
        peak = float(acc.max())
        normed = (acc / peak) if peak > 1e-6 else acc
        # Scale normalized [0,1] to [0,128] to map cold→dark to hot→bright
        # JET colormap: 0=dark blue, 128=bright cyan, 255=dark red
        # Using [0,128] ensures hot (1.0→128) is bright and cold (0→0) is dark
        heat_small = (np.clip(normed, 0.0, 1.0) * 128).astype(np.uint8)
        colored = cv2.applyColorMap(heat_small, cv2.COLORMAP_JET)
        return cv2.resize(colored, size)  # size = (width, height)

    def overlay(self, frame: np.ndarray) -> np.ndarray:
        """Overlay colorized heatmap on frame using addWeighted; return copy."""
        if not self.has_data:
            return frame.copy()
        h, w = frame.shape[:2]
        heat = self._colorized((w, h))
        return cv2.addWeighted(frame, 1.0, heat, self._alpha, 0.0)

    def export_png(self, path: Path) -> None:
        """Write colorized heatmap to PNG; write black square if accumulator empty; create parent dirs."""
        path.parent.mkdir(parents=True, exist_ok=True)
        if self._acc is None:
            image = np.zeros((10, 10, 3), dtype=np.uint8)
        else:
            h, w = self._acc.shape
            image = self._colorized((w, h))
        ok = cv2.imwrite(str(path), image)
        if not ok:
            raise OSError(f"failed to write heatmap PNG: {path}")

    def reset(self) -> None:
        """Clear accumulator."""
        self._acc = None
