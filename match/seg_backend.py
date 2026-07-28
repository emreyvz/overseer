"""YOLO-seg foreground backend for the Segmenter. Lazy-loads an ultralytics seg model; a
missing weight file or import failure makes ``available()`` False so the Segmenter uses its
deterministic ellipse fallback instead."""
from __future__ import annotations

from pathlib import Path

import numpy as np

# ultralytics class-name substrings we treat as each of our classes
_CLS_KEYWORDS = {
    "person": ("person",),
    "vehicle": ("car", "truck", "bus", "motorcycle", "bicycle", "train"),
    "animal": ("cat", "dog", "horse", "sheep", "cow", "bird", "bear", "elephant"),
}


class YoloSegBackend:
    def __init__(self, model_path: str | Path = "models/yolo11n-seg.pt",
                 device: str | None = None) -> None:
        self.model_path = Path(model_path)
        self._device = device
        self._model = None
        self._names: dict[int, str] = {}
        self._loaded = False
        self._ok = False

    def _load(self) -> bool:
        if self._loaded:
            return self._ok
        self._loaded = True
        try:
            if not self.model_path.exists():
                return False
            from ultralytics import YOLO
            self._model = YOLO(str(self.model_path))
            self._names = dict(self._model.names)
            self._ok = True
        except Exception:  # noqa: BLE001
            self._ok = False
        return self._ok

    def available(self) -> bool:
        return self._load()

    def mask(self, crop: np.ndarray, cls: str) -> np.ndarray | None:
        if not self.available() or crop is None or crop.size == 0:
            return None
        try:
            res = self._model.predict(crop, verbose=False, device=self._device)
            if not res:
                return None
            r = res[0]
            if r.masks is None:
                return None
            keywords = _CLS_KEYWORDS.get(cls, ())
            h, w = crop.shape[:2]
            union = np.zeros((h, w), dtype=bool)
            data = r.masks.data.cpu().numpy()          # (N, mh, mw)
            classes = r.boxes.cls.cpu().numpy().astype(int)
            import cv2
            for i, m in enumerate(data):
                name = self._names.get(int(classes[i]), "").lower()
                if keywords and not any(k in name for k in keywords):
                    continue
                mm = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST) > 0.5
                union |= mm
            return union if union.any() else None
        except Exception:  # noqa: BLE001
            return None
