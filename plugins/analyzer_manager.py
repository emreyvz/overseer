"""Registry and sequential runner for frame-level analyzers."""
from __future__ import annotations

import threading

from loguru import logger

from camera.frame_buffer import Frame
from plugins.analyzer import (
    AnalyzerEvent, BaseAnalyzer, EnvironmentContext, EnvironmentReadings,
)
from vision.metrics import ImageMetrics


class AnalyzerManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._analyzers: dict[str, BaseAnalyzer] = {}

    def register(self, analyzer: BaseAnalyzer) -> None:
        with self._lock:
            if analyzer.name in self._analyzers:
                raise ValueError(f"analyzer already registered: {analyzer.name}")
            self._analyzers[analyzer.name] = analyzer

    def get(self, name: str) -> BaseAnalyzer | None:
        with self._lock:
            return self._analyzers.get(name)

    def all(self) -> list[BaseAnalyzer]:
        with self._lock:
            return list(self._analyzers.values())

    def set_enabled(self, name: str, enabled: bool) -> None:
        with self._lock:
            analyzer = self._analyzers.get(name)
        if analyzer is not None:
            analyzer.enabled = enabled

    def analyze_frame(
        self, frame: Frame, metrics: ImageMetrics
    ) -> tuple[EnvironmentReadings, list[AnalyzerEvent]]:
        with self._lock:
            analyzers = list(self._analyzers.values())
        ctx = EnvironmentContext(metrics=metrics)
        readings = EnvironmentReadings()
        events: list[AnalyzerEvent] = []
        for analyzer in analyzers:
            if not analyzer.enabled:
                continue
            try:
                reading = analyzer.analyze(frame, ctx)
            except Exception:
                logger.exception("analyzer {} failed", analyzer.name)
                continue
            ctx.values.update(reading.values)
            ctx.labels.update(reading.labels)
            readings.values.update(reading.values)
            readings.labels.update(reading.labels)
            if reading.event is not None:
                events.append(reading.event)
        return readings, events

    def reset_all(self) -> None:
        for analyzer in self.all():
            try:
                analyzer.reset()
            except Exception:
                logger.exception("analyzer {} reset failed", analyzer.name)

    def close_all(self) -> None:
        for analyzer in self.all():
            try:
                analyzer.close()
            except Exception:
                logger.exception("analyzer {} close failed", analyzer.name)
