"""Snapshot files under dated folders with unique names."""
from __future__ import annotations

import itertools
import time
from pathlib import Path

import cv2
import numpy as np


class SnapshotService:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._counter = itertools.count()

    def save(self, image: np.ndarray, prefix: str = "manual") -> Path:
        now = time.localtime()
        day_dir = self._base_dir / time.strftime("%Y-%m-%d", now)
        day_dir.mkdir(parents=True, exist_ok=True)
        name = f"{prefix}_{time.strftime('%H%M%S', now)}_{next(self._counter):04d}.jpg"
        path = day_dir / name
        ok = cv2.imwrite(str(path), image)
        if not ok:
            raise OSError(f"failed to write snapshot: {path}")
        return path
