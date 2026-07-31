"""MOG2 background-subtraction motion detector plugin."""
from __future__ import annotations

import cv2
import numpy as np

from camera.frame_buffer import Frame
from core.config import Config
from plugins.base import BaseDetector, Detection


class MotionDetector(BaseDetector):
    name = "motion"
    display_name = "Motion Detection"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._min_area = int(config.get("detectors.motion.min_area", 500))
        self._subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=25, detectShadows=True
        )
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self.motion_percent: float = 0.0
        self.last_mask: np.ndarray | None = None
        self.enabled = True  # gated by the operator's MOTION module toggle

    def process(self, frame: Frame) -> list[Detection]:
        if not self.enabled:
            # Operator turned MOTION off: skip MOG2 + contour work entirely.
            self.motion_percent = 0.0
            self.last_mask = None
            return []
        mask = self._subtractor.apply(frame.image)
        # Discard shadows (127), keep only definite foreground (255)
        _, mask = cv2.threshold(mask, 200, 255, cv2.THRESH_BINARY)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, self._kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, self._kernel)
        self.motion_percent = float(np.count_nonzero(mask)) * 100.0 / mask.size
        self.last_mask = mask
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        detections: list[Detection] = []
        for contour in contours:
            if cv2.contourArea(contour) < self._min_area:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            detections.append(Detection(
                label="motion", confidence=1.0, bbox=(x, y, x + w, y + h),
                category="motion",
            ))
        return detections
