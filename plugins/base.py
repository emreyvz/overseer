"""Detector plugin contract."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from camera.frame_buffer import Frame
from core.config import Config


@dataclass
class Detection:
    label: str
    confidence: float
    bbox: tuple[int, int, int, int]
    category: str
    track_id: int | None = None


class BaseDetector(ABC):
    name: str
    display_name: str

    def __init__(self, config: Config) -> None:
        self.config = config
        self.enabled: bool = bool(config.get(f"detectors.{self.name}.enabled", True))

    @abstractmethod
    def process(self, frame: Frame) -> list[Detection]:
        ...

    def close(self) -> None:
        pass
