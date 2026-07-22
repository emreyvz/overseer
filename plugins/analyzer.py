"""Frame-level analyzer plugin contract (scalar/label readings, not boxes)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from camera.frame_buffer import Frame
from core.config import Config
from events.types import EventType
from vision.metrics import ImageMetrics


@dataclass
class AnalyzerEvent:
    label: str
    metadata: dict[str, object] = field(default_factory=dict)
    event_type: EventType | None = None


@dataclass
class AnalyzerReading:
    values: dict[str, float] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    event: AnalyzerEvent | None = None


@dataclass
class EnvironmentContext:
    metrics: ImageMetrics
    values: dict[str, float] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    is_day: bool | None = None


@dataclass
class EnvironmentReadings:
    values: dict[str, float] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)


class BaseAnalyzer(ABC):
    name: str
    display_name: str

    def __init__(self, config: Config) -> None:
        self.config = config
        self.enabled: bool = bool(config.get(f"analyzers.{self.name}.enabled", True))

    @abstractmethod
    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        ...

    def reset(self) -> None:
        """Clear mutable state back to construction defaults.

        Called when the active source changes so EMA/hysteresis state and
        one-frame-lookback samples (e.g. optical flow) from the previous
        source don't leak into the new one and cause spurious transition
        events. Default no-op; analyzers with mutable state override this.
        """

    def close(self) -> None:
        pass
