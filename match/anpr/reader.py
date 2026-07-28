"""Plate OCR adapter. Wraps EasyOCR or PaddleOCR (whichever is installed), lazily. When
neither is present it reports ``available() == False`` and reads nothing, so vehicles fall
back to appearance ReID. The reader returns per-crop (text, confidence) pairs; temporal
consistency and confidence are enforced downstream by PlateVoter — this adapter never
asserts an identity on its own."""
from __future__ import annotations

import numpy as np


class OcrPlateReader:
    def __init__(self, langs: tuple[str, ...] = ("en",), gpu: bool = True) -> None:
        self._langs = list(langs)
        self._gpu = gpu
        self._impl: str | None = None
        self._reader = None
        self._loaded = False
        self._ok = False

    def _load(self) -> bool:
        if self._loaded:
            return self._ok
        self._loaded = True
        try:
            import easyocr
            self._reader = easyocr.Reader(self._langs, gpu=self._gpu)
            self._impl = "easyocr"
            self._ok = True
            return True
        except Exception:  # noqa: BLE001
            pass
        try:
            from paddleocr import PaddleOCR
            self._reader = PaddleOCR(use_angle_cls=True, lang=self._langs[0], show_log=False)
            self._impl = "paddleocr"
            self._ok = True
            return True
        except Exception:  # noqa: BLE001
            self._ok = False
        return self._ok

    def available(self) -> bool:
        return self._load()

    def __call__(self, crop: np.ndarray) -> list[tuple[str, float]]:
        if not self.available() or crop is None or getattr(crop, "size", 0) == 0:
            return []
        try:
            if self._impl == "easyocr":
                return [(str(text), float(conf))
                        for (_box, text, conf) in self._reader.readtext(crop)]
            # paddleocr: [[ [box, (text, conf)], ... ]]
            out: list[tuple[str, float]] = []
            for line in (self._reader.ocr(crop, cls=True) or []):
                for _box, (text, conf) in (line or []):
                    out.append((str(text), float(conf)))
            return out
        except Exception:  # noqa: BLE001 - OCR failure is just "no reads"
            return []
