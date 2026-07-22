"""Meteor detection: transient thin bright streaks at night (rejects blobs/persistent)."""
from __future__ import annotations

import cv2
import numpy as np

from camera.frame_buffer import Frame
from core.config import Config
from events.types import EventType
from plugins.analyzer import (
    AnalyzerEvent, AnalyzerReading, BaseAnalyzer, EnvironmentContext,
)


class MeteorAnalyzer(BaseAnalyzer):
    name = "meteor"
    display_name = "Meteor"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._threshold = int(config.get("analyzers.meteor.diff_threshold", 40))
        self._min_elongation = float(config.get("analyzers.meteor.min_elongation", 3.0))
        self._min_area = float(config.get("analyzers.meteor.min_area", 8))
        self._max_area = float(config.get("analyzers.meteor.max_area", 400))
        self._prev: np.ndarray | None = None

    def reset(self) -> None:
        self._prev = None

    def _small_gray(self, frame: Frame) -> np.ndarray:
        gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        height = int(320 * gray.shape[0] / gray.shape[1])
        return cv2.resize(gray, (320, height))

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        if ctx.is_day is not False:  # run only at confirmed night
            self._prev = None
            return AnalyzerReading(values={"meteor_detected": 0.0})

        small = self._small_gray(frame)
        detected = 0.0
        event: AnalyzerEvent | None = None
        if self._prev is not None and self._prev.shape == small.shape:
            diff = small.astype(np.int16) - self._prev.astype(np.int16)
            new_bright = (diff > self._threshold).astype(np.uint8) * 255
            contours, _ = cv2.findContours(
                new_bright, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
            )
            for contour in contours:
                (_, (w, h), _) = cv2.minAreaRect(contour)
                if w <= 0 or h <= 0:
                    continue
                # Use the min-area-rect footprint (w*h), NOT cv2.contourArea: a 1px-thin
                # streak has ~zero contourArea but a real rect footprint (length*thickness).
                rect_area = w * h
                if rect_area < self._min_area or rect_area > self._max_area:
                    continue
                elongation = max(w, h) / min(w, h)
                if elongation >= self._min_elongation:
                    detected = 1.0
                    event = AnalyzerEvent(label="Meteor", event_type=EventType.METEOR)
                    break
        self._prev = small
        return AnalyzerReading(values={"meteor_detected": detected}, event=event)
