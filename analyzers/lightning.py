"""Lightning detection from sudden frame-brightness spikes above a rolling baseline."""
from __future__ import annotations

from camera.frame_buffer import Frame
from core.config import Config
from events.types import EventType
from plugins.analyzer import (
    AnalyzerEvent, AnalyzerReading, BaseAnalyzer, EnvironmentContext,
)


class LightningAnalyzer(BaseAnalyzer):
    name = "lightning"
    display_name = "Lightning"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._spike_delta = float(config.get("analyzers.lightning.spike_delta", 40.0))
        self._spike_ratio = float(config.get("analyzers.lightning.spike_ratio", 1.5))
        self._refractory = float(
            config.get("analyzers.lightning.refractory_seconds", 2.0)
        )
        self._baseline: float | None = None
        self._last_flash_at: float | None = None

    def reset(self) -> None:
        self._baseline = None
        self._last_flash_at = None

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        brightness = ctx.metrics.brightness
        if self._baseline is None:
            self._baseline = brightness
            return AnalyzerReading(values={"lightning_flash": 0.0})

        is_spike = (
            brightness > self._baseline + self._spike_delta
            and brightness > self._baseline * self._spike_ratio
        )
        flash = 0.0
        event: AnalyzerEvent | None = None
        now = frame.timestamp
        if is_spike and (
            self._last_flash_at is None
            or now - self._last_flash_at >= self._refractory
        ):
            flash = 1.0
            self._last_flash_at = now
            event = AnalyzerEvent(
                label="Lightning", event_type=EventType.LIGHTNING,
                metadata={"brightness": round(brightness, 1)},
            )
        # Slow baseline update; a single spike frame barely moves it.
        self._baseline = 0.9 * self._baseline + 0.1 * brightness
        return AnalyzerReading(values={"lightning_flash": flash}, event=event)
