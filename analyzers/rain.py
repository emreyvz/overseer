"""Rain estimation from temporal vertical-streak analysis of frame differences."""
from __future__ import annotations

import cv2
import numpy as np

from camera.frame_buffer import Frame
from core.config import Config
from plugins.analyzer import (
    AnalyzerEvent, AnalyzerReading, BaseAnalyzer, EnvironmentContext,
)


def _band_weight(fraction: float) -> float:
    if fraction < 0.005:
        return max(0.0, fraction / 0.005)  # decays to 0 at zero motion
    if fraction <= 0.2:
        return 1.0
    return max(0.0, 1.0 - (fraction - 0.2) / 0.2)  # decays to 0 by fraction=0.4


class RainAnalyzer(BaseAnalyzer):
    name = "rain"
    display_name = "Rain"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._threshold = int(config.get("analyzers.rain.diff_threshold", 25))
        self._onset = float(config.get("analyzers.rain.onset", 0.5))
        self._clear = float(config.get("analyzers.rain.clear", 0.3))
        self._min_onset = float(config.get("analyzers.rain.min_onset_seconds", 3.0))
        self._kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 7))
        self._prev: np.ndarray | None = None
        self._smoothed = 0.0
        self._raining = False
        self._above_since: float | None = None

    def reset(self) -> None:
        self._prev = None
        self._smoothed = 0.0
        self._raining = False
        self._above_since = None

    def _small_gray(self, frame: Frame) -> np.ndarray:
        gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        height = int(320 * gray.shape[0] / gray.shape[1])
        return cv2.resize(gray, (320, height))

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        small = self._small_gray(frame)
        score = 0.0
        if self._prev is not None and self._prev.shape == small.shape:
            diff = cv2.absdiff(small, self._prev)
            mask = (diff > self._threshold).astype(np.uint8) * 255
            total = int(np.count_nonzero(mask))
            if total > 0:
                vertical = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
                vratio = float(np.count_nonzero(vertical)) / float(total)
                fraction = float(total) / float(mask.size)
                score = vratio * _band_weight(fraction)
        self._prev = small
        self._smoothed = 0.7 * self._smoothed + 0.3 * score
        probability = self._smoothed

        event: AnalyzerEvent | None = None
        now = frame.timestamp
        if probability >= self._onset:
            if self._above_since is None:
                self._above_since = now
            if not self._raining and now - self._above_since >= self._min_onset:
                self._raining = True
                event = AnalyzerEvent(label="Rain started")
        else:
            self._above_since = None
            if self._raining and probability <= self._clear:
                self._raining = False
                event = AnalyzerEvent(label="Rain stopped")

        return AnalyzerReading(values={"rain_probability": probability}, event=event)
