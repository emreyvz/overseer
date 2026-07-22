"""Camera-tampering detection: lens defocus, obstruction and camera movement."""
from __future__ import annotations

import cv2
import numpy as np

from camera.frame_buffer import Frame
from core.config import Config
from events.types import EventType
from plugins.analyzer import (
    AnalyzerEvent, AnalyzerReading, BaseAnalyzer, EnvironmentContext,
)

_REF_SIZE = (64, 36)


class TamperingAnalyzer(BaseAnalyzer):
    name = "tampering"
    display_name = "Tampering"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._defocus_sharpness = float(
            config.get("analyzers.tampering.defocus_sharpness", 60.0)
        )
        self._defocus_clear = float(
            config.get("analyzers.tampering.defocus_clear", 120.0)
        )
        self._obstruction_contrast = float(
            config.get("analyzers.tampering.obstruction_contrast", 8.0)
        )
        self._obstruction_clear = float(
            config.get("analyzers.tampering.obstruction_clear", 16.0)
        )
        self._move_mad = float(config.get("analyzers.tampering.move_mad", 45.0))
        self._move_frames = int(config.get("analyzers.tampering.move_frames", 3))

        self._blurred = False
        self._blocked = False
        self._ref: np.ndarray | None = None
        self._move_count = 0

    def reset(self) -> None:
        self._blurred = False
        self._blocked = False
        self._ref = None
        self._move_count = 0

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        # -- obstruction (highest priority) --------------------------------
        contrast = ctx.metrics.contrast
        obstruction_event: AnalyzerEvent | None = None
        if not self._blocked and contrast < self._obstruction_contrast:
            self._blocked = True
            obstruction_event = AnalyzerEvent(
                label="View obstructed", event_type=EventType.OBSTRUCTION
            )
        elif self._blocked and contrast > self._obstruction_clear:
            self._blocked = False

        # -- defocus ----------------------------------------------------------
        sharpness = ctx.metrics.sharpness
        defocus_event: AnalyzerEvent | None = None
        if not self._blurred and sharpness < self._defocus_sharpness:
            self._blurred = True
            defocus_event = AnalyzerEvent(
                label="Focus loss", event_type=EventType.DEFOCUS
            )
        elif self._blurred and sharpness > self._defocus_clear:
            self._blurred = False

        # -- camera moved -------------------------------------------------------
        gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        cur = cv2.resize(gray, _REF_SIZE).astype(np.float32)
        mad = 0.0
        moved_event: AnalyzerEvent | None = None
        if self._ref is None:
            self._ref = cur
        else:
            mad = float(np.abs(cur - self._ref).mean())
            if mad > self._move_mad:
                self._move_count += 1
                if self._move_count >= self._move_frames:
                    moved_event = AnalyzerEvent(
                        label="Camera moved", event_type=EventType.CAMERA_MOVED
                    )
                    self._ref = cur
                    self._move_count = 0
            else:
                self._move_count = 0
                self._ref = 0.95 * self._ref + 0.05 * cur

        event = obstruction_event or defocus_event or moved_event
        return AnalyzerReading(
            values={"sharpness": float(sharpness), "tamper_mad": mad},
            event=event,
        )
