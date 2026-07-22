"""Registry and runner for detector plugins.

Thread-safe: the UI thread registers/toggles plugins (e.g. YOLO plugins arrive
after async model load) while the analysis thread runs process_frame.
"""
from __future__ import annotations

import threading

from loguru import logger

from camera.frame_buffer import Frame
from plugins.base import BaseDetector, Detection


class PluginManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._detectors: dict[str, BaseDetector] = {}

    def register(self, detector: BaseDetector) -> None:
        with self._lock:
            if detector.name in self._detectors:
                raise ValueError(f"detector already registered: {detector.name}")
            self._detectors[detector.name] = detector

    def get(self, name: str) -> BaseDetector | None:
        with self._lock:
            return self._detectors.get(name)

    def all(self) -> list[BaseDetector]:
        with self._lock:
            return list(self._detectors.values())

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            detector = self._detectors.get(name)
        if detector is not None:
            detector.enabled = enabled

    def process_frame(self, frame: Frame) -> dict[str, list[Detection]]:
        with self._lock:
            detectors = list(self._detectors.items())
        results: dict[str, list[Detection]] = {}
        for name, detector in detectors:
            if not detector.enabled:
                continue
            try:
                results[name] = detector.process(frame)
            except Exception:
                logger.exception("detector {} failed", name)
                results[name] = []
        return results

    def close_all(self) -> None:
        for detector in self.all():
            try:
                detector.close()
            except Exception:
                logger.exception("detector {} close failed", detector.name)
