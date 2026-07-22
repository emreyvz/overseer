"""Satellite detection: slow, straight-moving bright points tracked over frames (night)."""
from __future__ import annotations

import math

import cv2

from camera.frame_buffer import Frame
from core.config import Config
from events.types import EventType
from plugins.analyzer import (
    AnalyzerEvent, AnalyzerReading, BaseAnalyzer, EnvironmentContext,
)


class _Track:
    __slots__ = ("points", "missed", "reported")

    def __init__(self, point: tuple[float, float]) -> None:
        self.points: list[tuple[float, float]] = [point]
        self.missed = 0
        self.reported = False


class SatelliteAnalyzer(BaseAnalyzer):
    name = "satellite"
    display_name = "Satellite"

    def __init__(self, config: Config) -> None:
        super().__init__(config)
        self._threshold = int(config.get("analyzers.satellite.diff_threshold", 30))
        self._min_len = int(config.get("analyzers.satellite.min_track_length", 6))
        self._min_speed = float(config.get("analyzers.satellite.min_speed", 0.5))
        self._max_speed = float(config.get("analyzers.satellite.max_speed", 8.0))
        self._max_dir_std = float(
            config.get("analyzers.satellite.max_direction_std_deg", 15.0)
        )
        self._tracks: list[_Track] = []
        # Bounded trailing window: caps per-track memory and keeps _is_satellite
        # O(cap) instead of O(history), while staying large enough that a valid
        # straight-slow track (needs >= _min_len points) can still confirm.
        self._history_cap = max(self._min_len + 1, 2 * self._min_len)

    def reset(self) -> None:
        self._tracks = []

    def _bright_points(self, frame: Frame) -> list[tuple[float, float]]:
        gray = cv2.cvtColor(frame.image, cv2.COLOR_BGR2GRAY)
        height = int(320 * gray.shape[0] / gray.shape[1])
        small = cv2.resize(gray, (320, height))
        _, mask = cv2.threshold(small, self._threshold, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        points: list[tuple[float, float]] = []
        for contour in contours:
            if cv2.contourArea(contour) > 60:  # ignore large blobs (not point-like)
                continue
            moments = cv2.moments(contour)
            if moments["m00"] == 0:
                continue
            points.append((moments["m10"] / moments["m00"],
                           moments["m01"] / moments["m00"]))
        return points

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        if ctx.is_day is not False:
            self._tracks = []
            return AnalyzerReading(values={"satellite_detected": 0.0})

        points = self._bright_points(frame)
        gate = self._max_speed * 2.0
        updated: set[int] = set()
        for point in points:
            best = self._nearest_track(point, gate, updated)
            if best is None:
                self._tracks.append(_Track(point))
                updated.add(len(self._tracks) - 1)  # don't age a track in its creation frame
            else:
                track = self._tracks[best]
                track.points.append(point)
                if len(track.points) > self._history_cap:
                    del track.points[0]  # bounded trailing window: memory + O(cap) eval
                track.missed = 0
                updated.add(best)
        for index, track in enumerate(self._tracks):
            if index not in updated:
                track.missed += 1
        self._tracks = [t for t in self._tracks if t.missed <= 3]

        detected = 0.0
        event: AnalyzerEvent | None = None
        for track in self._tracks:
            if not track.reported and self._is_satellite(track):
                track.reported = True
                detected = 1.0
                event = AnalyzerEvent(label="Satellite", event_type=EventType.SATELLITE)
                break
        return AnalyzerReading(values={"satellite_detected": detected}, event=event)

    def _nearest_track(self, point: tuple[float, float], gate: float,
                       used: set[int]) -> int | None:
        best_index: int | None = None
        best_dist = gate
        for index, track in enumerate(self._tracks):
            if index in used:
                continue
            last = track.points[-1]
            dist = math.hypot(point[0] - last[0], point[1] - last[1])
            if dist < best_dist:
                best_dist = dist
                best_index = index
        return best_index

    def _is_satellite(self, track: _Track) -> bool:
        if len(track.points) < self._min_len:
            return False
        speeds: list[float] = []
        angles: list[float] = []
        for (x0, y0), (x1, y1) in zip(track.points, track.points[1:]):
            dx, dy = x1 - x0, y1 - y0
            speed = math.hypot(dx, dy)
            speeds.append(speed)
            angles.append(math.atan2(dy, dx))
        if any(s < self._min_speed or s > self._max_speed for s in speeds):
            return False
        return _angle_std_deg(angles) < self._max_dir_std


def _angle_std_deg(angles: list[float]) -> float:
    sin_sum = sum(math.sin(a) for a in angles)
    cos_sum = sum(math.cos(a) for a in angles)
    mean = math.atan2(sin_sum, cos_sum)
    variance = sum((math.atan2(math.sin(a - mean), math.cos(a - mean))) ** 2
                   for a in angles) / len(angles)
    return math.degrees(math.sqrt(variance))
