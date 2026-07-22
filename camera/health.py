"""Detects dead (no frames) and frozen (identical frames) streams."""
from __future__ import annotations

import cv2

from camera.frame_buffer import Frame


class HealthMonitor:
    def __init__(self, freeze_timeout: float = 10.0) -> None:
        self._freeze_timeout = freeze_timeout
        self._last_frame_at: float | None = None
        self._last_change_at: float | None = None
        self._signature: bytes | None = None

    def reset(self, now: float) -> None:
        """Called on new connection: counter starts from 'now'."""
        self._last_frame_at = now
        self._last_change_at = now
        self._signature = None

    def observe(self, frame: Frame) -> None:
        gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        signature = cv2.resize(gray, (16, 16)).tobytes()
        self._last_frame_at = frame.timestamp
        if signature != self._signature:
            self._signature = signature
            self._last_change_at = frame.timestamp

    def check(self, now: float) -> list[str]:
        if self._last_frame_at is None:
            return []
        if now - self._last_frame_at > self._freeze_timeout:
            return ["no_frames"]
        if self._last_change_at is not None and (
            now - self._last_change_at > self._freeze_timeout
        ):
            return ["frozen"]
        return []
