"""Motion detector plugin: MOG2 background subtraction UNIONed with frame differencing.

MOG2 alone models the background and, crucially, *adapts to repetitive motion* — after a few
hundred frames it learns swaying trees, flickering lights and especially SEA WAVES into the
background, so persistent movement stops registering as motion. Frame differencing (|frame_t −
frame_{t-1}|) has no memory, so it always reacts to any change — waves, a waving hand — but is
noisy on its own. We take the UNION: MOG2 for clean object foreground + frame-diff for continuous
motion, giving a motion signal that stays alive on waves while still finding discrete blobs.
"""
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
        # frame-diff sensitivity: per-pixel grey delta above this counts as motion (lower = more
        # sensitive). Keeps repetitive motion (waves) alive that MOG2 would absorb.
        self._diff_thresh = int(config.get("detectors.motion.diff_thresh", 16))
        self._subtractor = cv2.createBackgroundSubtractorMOG2(
            history=500, varThreshold=25, detectShadows=True
        )
        self._kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        self._prev_gray: np.ndarray | None = None
        self.motion_percent: float = 0.0
        self.last_mask: np.ndarray | None = None

    def process(self, frame: Frame) -> list[Detection]:
        img = frame.image
        # --- MOG2 foreground (definite foreground only; drop shadows at 127) ---
        mog = self._subtractor.apply(img)
        _, mog = cv2.threshold(mog, 200, 255, cv2.THRESH_BINARY)
        # --- frame difference (always reacts to change; catches waves / repetitive motion) ---
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (5, 5), 0)
        diff = np.zeros_like(gray)
        if self._prev_gray is not None and self._prev_gray.shape == gray.shape:
            d = cv2.absdiff(gray, self._prev_gray)
            _, diff = cv2.threshold(d, self._diff_thresh, 255, cv2.THRESH_BINARY)
        self._prev_gray = gray
        # --- union + clean up ---
        mask = cv2.bitwise_or(mog, diff)
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
