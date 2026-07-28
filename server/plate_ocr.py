"""Factory for the plate OCR backend, kept separate so its (lazy, heavy) EasyOCR import
lives in one place and is easy to stub in tests."""
from __future__ import annotations

from match.anpr.reader import OcrPlateReader


def default_plate_reader(langs: tuple[str, ...] = ("en",), gpu: bool = True) -> OcrPlateReader:
    return OcrPlateReader(langs=langs, gpu=gpu)
