"""Fog estimation from contrast loss, edge density and Laplacian sharpness."""
from __future__ import annotations

import cv2
import numpy as np

from camera.frame_buffer import Frame
from core.config import Config
from plugins.analyzer import (
    AnalyzerEvent, AnalyzerReading, BaseAnalyzer, EnvironmentContext,
)


def _lack(value: float, reference: float) -> float:
    if reference <= 0:
        return 0.0
    return 1.0 - min(1.0, value / reference)


class FogAnalyzer(BaseAnalyzer):
    name = "fog"
    display_name = "Fog"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._contrast_ref = float(config.get("analyzers.fog.contrast_ref", 50.0))
        self._edge_ref = float(config.get("analyzers.fog.edge_ref", 0.05))
        self._sharpness_ref = float(config.get("analyzers.fog.sharpness_ref", 300.0))
        self._onset = float(config.get("analyzers.fog.onset", 0.6))
        self._clear = float(config.get("analyzers.fog.clear", 0.3))
        self._smoothed: float | None = None
        self._foggy = False

    def reset(self) -> None:
        self._smoothed = None
        self._foggy = False

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        if gray.shape[1] > 640:
            gray = cv2.resize(gray, (0, 0), fx=0.5, fy=0.5)
        edges = cv2.Canny(gray, 50, 150)
        edge_density = float(np.count_nonzero(edges)) / float(edges.size)

        score = (
            0.4 * _lack(ctx.metrics.contrast, self._contrast_ref)
            + 0.3 * _lack(edge_density, self._edge_ref)
            + 0.3 * _lack(ctx.metrics.sharpness, self._sharpness_ref)
        )
        score = max(0.0, min(1.0, score))
        self._smoothed = (
            score if self._smoothed is None else 0.7 * self._smoothed + 0.3 * score
        )
        probability = self._smoothed

        event: AnalyzerEvent | None = None
        if not self._foggy and probability >= self._onset:
            self._foggy = True
            event = AnalyzerEvent(label="Fog started")
        elif self._foggy and probability <= self._clear:
            self._foggy = False
            event = AnalyzerEvent(label="Fog cleared")

        return AnalyzerReading(
            values={"fog_probability": probability, "visibility": 1.0 - probability},
            event=event,
        )
