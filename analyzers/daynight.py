"""Day/night classification from smoothed frame brightness with hysteresis."""
from __future__ import annotations

from camera.frame_buffer import Frame
from core.config import Config
from events.types import EventType
from plugins.analyzer import (
    AnalyzerEvent, AnalyzerReading, BaseAnalyzer, EnvironmentContext,
)


class DayNightAnalyzer(BaseAnalyzer):
    name = "daynight"
    display_name = "Day/Night"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._night_below = float(config.get("analyzers.daynight.night_below", 50.0))
        self._day_above = float(config.get("analyzers.daynight.day_above", 70.0))
        self._mid = (self._night_below + self._day_above) / 2.0
        self._smoothed: float | None = None
        self._is_day: bool | None = None

    def reset(self) -> None:
        self._smoothed = None
        self._is_day = None

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        brightness = ctx.metrics.brightness
        self._smoothed = (
            brightness if self._smoothed is None
            else 0.8 * self._smoothed + 0.2 * brightness
        )
        smoothed = self._smoothed
        previous = self._is_day
        if smoothed < self._night_below:
            is_day = False
        elif smoothed > self._day_above:
            is_day = True
        else:
            is_day = previous if previous is not None else smoothed >= self._mid
        self._is_day = is_day
        ctx.is_day = is_day

        if smoothed < self._night_below:
            label = "Night"
        elif smoothed > self._day_above:
            label = "Day"
        else:
            label = "Twilight"

        if smoothed > self._day_above:
            solar_phase = "Day"
        elif smoothed < self._night_below:
            solar_phase = "Night"
        else:
            solar_phase = "Golden Hour" if smoothed >= self._mid else "Blue Hour"

        event: AnalyzerEvent | None = None
        if previous is not None and previous != is_day:
            if is_day:
                event = AnalyzerEvent(label="Sun rose", event_type=EventType.SUNRISE)
            else:
                event = AnalyzerEvent(label="Sun set", event_type=EventType.SUNSET)
        return AnalyzerReading(
            values={"brightness_smoothed": smoothed},
            labels={"daynight": label, "solar_phase": solar_phase},
            event=event,
        )
