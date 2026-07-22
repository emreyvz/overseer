"""Weather classification aggregating environment readings (runs last)."""
from __future__ import annotations

import cv2
import numpy as np

from camera.frame_buffer import Frame
from core.config import Config
from plugins.analyzer import (
    AnalyzerEvent, AnalyzerReading, BaseAnalyzer, EnvironmentContext,
)


class WeatherAnalyzer(BaseAnalyzer):
    name = "weather"
    display_name = "Weather"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._fog_high = float(config.get("analyzers.weather.fog_high", 0.6))
        self._rain_high = float(config.get("analyzers.weather.rain_high", 0.5))
        self._wind_high = float(config.get("analyzers.weather.wind_high", 0.5))
        self._cloud_high = float(config.get("analyzers.weather.cloud_high", 0.5))
        self._min_dwell = float(
            config.get("analyzers.weather.min_dwell_seconds", 30.0)
        )
        self._smoothed_cloud: float | None = None
        self._label: str | None = None
        self._candidate: str | None = None
        self._candidate_since: float = 0.0

    def reset(self) -> None:
        self._smoothed_cloud = None
        self._label = None
        self._candidate = None
        self._candidate_since = 0.0

    @staticmethod
    def _cloud_coverage(image: np.ndarray) -> float:
        height = image.shape[0]
        sky = image[: max(1, height // 3)]
        hsv = cv2.cvtColor(sky, cv2.COLOR_BGR2HSV)
        value = hsv[..., 2].astype(np.float32) / 255.0
        saturation = hsv[..., 1].astype(np.float32) / 255.0
        bright = value > 0.4
        bright_count = int(np.count_nonzero(bright))
        if bright_count == 0:
            return 0.0
        low_sat = (saturation < 0.25) & bright
        return float(np.count_nonzero(low_sat)) / float(bright_count)

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        raw_cloud = self._cloud_coverage(frame.image)
        self._smoothed_cloud = (
            raw_cloud if self._smoothed_cloud is None
            else 0.8 * self._smoothed_cloud + 0.2 * raw_cloud
        )
        cloud = self._smoothed_cloud
        fog = ctx.values.get("fog_probability", 0.0)
        rain = ctx.values.get("rain_probability", 0.0)
        wind = ctx.values.get("wind_strength", 0.0)
        is_day = ctx.is_day if ctx.is_day is not None else ctx.metrics.brightness >= 60.0

        if not is_day:
            candidate = "Night"
        elif fog >= self._fog_high:
            candidate = "Foggy"
        elif rain >= self._rain_high and wind >= self._wind_high:
            candidate = "Storm"
        elif rain >= self._rain_high:
            candidate = "Rainy"
        elif cloud >= self._cloud_high:
            candidate = "Cloudy"
        else:
            candidate = "Sunny"

        event: AnalyzerEvent | None = None
        if self._label is None:
            # First frame: commit immediately, no event.
            self._label = candidate
            self._candidate = candidate
            self._candidate_since = frame.timestamp
        else:
            if candidate != self._candidate:
                self._candidate = candidate
                self._candidate_since = frame.timestamp
            if (
                candidate != self._label
                and frame.timestamp - self._candidate_since >= self._min_dwell
            ):
                event = AnalyzerEvent(label=f"Weather changed: {candidate}")
                self._label = candidate

        return AnalyzerReading(
            values={"cloud_coverage": cloud},
            labels={"weather": self._label},
            event=event,
        )
