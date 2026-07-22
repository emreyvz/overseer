"""Wind estimation from dense optical flow (Farneback) between frames."""
from __future__ import annotations

import cv2
import numpy as np

from camera.frame_buffer import Frame
from core.config import Config
from plugins.analyzer import (
    AnalyzerEvent, AnalyzerReading, BaseAnalyzer, EnvironmentContext,
)

# 0=East, 45=NE, 90=North, ... (image y is down; direction uses -sy).
_COMPASS = ["D", "KD", "K", "KB", "B", "GB", "G", "GD"]


class WindAnalyzer(BaseAnalyzer):
    name = "wind"
    display_name = "Wind"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._max_flow = float(config.get("analyzers.wind.max_flow", 6.0))
        self._strong = float(config.get("analyzers.wind.strong", 0.5))
        self._prev: np.ndarray | None = None
        self._smoothed = 0.0
        self._windy = False

    def reset(self) -> None:
        self._prev = None
        self._smoothed = 0.0
        self._windy = False

    @staticmethod
    def _to_compass(degrees: float) -> str:
        index = int(((degrees + 22.5) % 360) // 45)
        return _COMPASS[index]

    def _small_gray(self, frame: Frame) -> np.ndarray:
        gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        height = int(320 * gray.shape[0] / gray.shape[1])
        return cv2.resize(gray, (320, height))

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        small = self._small_gray(frame)
        strength = 0.0
        direction = 0.0
        if self._prev is not None and self._prev.shape == small.shape:
            flow = cv2.calcOpticalFlowFarneback(
                self._prev, small, None, 0.5, 3, 15, 3, 5, 1.2, 0
            )
            fx = flow[..., 0]
            fy = flow[..., 1]
            magnitude = float(np.sqrt(fx * fx + fy * fy).mean())
            strength = min(1.0, magnitude / self._max_flow) if self._max_flow > 0 else 0.0
            sum_x = float(fx.sum())
            sum_y = float(fy.sum())
            if sum_x != 0.0 or sum_y != 0.0:
                direction = float(np.degrees(np.arctan2(-sum_y, sum_x)) % 360.0)
        self._prev = small
        self._smoothed = 0.7 * self._smoothed + 0.3 * strength
        smoothed = self._smoothed

        event: AnalyzerEvent | None = None
        if not self._windy and smoothed >= self._strong:
            self._windy = True
            event = AnalyzerEvent(label="Wind picked up")
        elif self._windy and smoothed < self._strong * 0.6:
            self._windy = False

        return AnalyzerReading(
            values={"wind_strength": smoothed, "wind_direction_deg": direction},
            labels={"wind_direction": self._to_compass(direction)},
            event=event,
        )
