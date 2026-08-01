"""Per-track position buffer -> running / stopped / U-turn. Worker-thread only."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from core.config import Config
from events.types import EventType
from plugins.base import Detection


@dataclass
class TrajectoryEvent:
    event_type: EventType
    track_id: int
    label: str
    metadata: dict = field(default_factory=dict)


@dataclass
class _Track:
    points: deque
    running_fired: bool = False
    stopped_fired: bool = False
    uturn_fired: bool = False
    last_seen: float = 0.0
    # Stationarity is tracked with an anchor + last-moved time (O(1)) instead of
    # a time window, so STOPPED fires under real wall-clock timestamps and does
    # not depend on the point buffer spanning stopped_seconds.
    anchor: tuple[int, int] | None = None
    last_moved: float = 0.0


def _bottom_center(bbox: tuple[int, int, int, int]) -> tuple[int, int]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) // 2, y2)


class TrajectoryMonitor:
    def __init__(self, config: Config) -> None:
        self._buffer_size = int(config.get("trajectory.buffer_size", 30))
        self._expire = float(config.get("trajectory.expire_seconds", 5.0))
        self._running_speed = float(config.get("trajectory.running_speed", 250.0))
        self._stopped_seconds = float(config.get("trajectory.stopped_seconds", 5.0))
        self._stopped_eps = float(config.get("trajectory.stopped_eps", 15.0))
        self._uturn_window = int(config.get("trajectory.uturn_window", 12))
        self._uturn_angle = float(config.get("trajectory.uturn_angle", 150.0))
        # each leg of a real U-turn must be an actual move, not detector jitter on a
        # near-stationary track (px). Without this, jitter fired constant false U-turns.
        self._uturn_min_disp = float(config.get("trajectory.uturn_min_disp", 28.0))
        self._tracks: dict[int, _Track] = {}

    def reset(self) -> None:
        self._tracks.clear()

    def process(self, detections: list[Detection],
                now: float) -> list[TrajectoryEvent]:
        events: list[TrajectoryEvent] = []
        for det in detections:
            if det.track_id is None or det.category not in ("person", "vehicle"):
                continue
            tid = det.track_id
            tr = self._tracks.get(tid)
            if tr is None:
                tr = _Track(points=deque(maxlen=self._buffer_size))
                self._tracks[tid] = tr
            pos = _bottom_center(det.bbox)
            tr.points.append((pos, now))
            tr.last_seen = now
            if tr.anchor is None or math.dist(pos, tr.anchor) > self._stopped_eps:
                tr.anchor = pos
                tr.last_moved = now
                tr.stopped_fired = False
            events += self._check_running(tr, tid)
            events += self._check_stopped(tr, tid, now)
            events += self._check_uturn(tr, tid)
        self._expire_tracks(now)
        return events

    def _check_running(self, tr: "_Track", tid: int) -> list[TrajectoryEvent]:
        pts = list(tr.points)
        if len(pts) < 3:
            return []
        # Median inter-frame speed over the last few frames, not a single frame: a detection jump
        # (tracker id swap, bbox jitter) spikes one frame's speed and used to fire a false RUNNING.
        recent = pts[-5:]
        speeds = [math.dist(pa, pb) / (tb - ta)
                  for (pa, ta), (pb, tb) in zip(recent, recent[1:]) if tb - ta > 0]
        if not speeds:
            return []
        speeds.sort()
        med = speeds[len(speeds) // 2]
        if med < self._running_speed * 0.6:
            tr.running_fired = False
        if med >= self._running_speed and not tr.running_fired:
            tr.running_fired = True
            return [TrajectoryEvent(EventType.RUNNING, tid, "running",
                                    {"speed": round(med, 1)})]
        return []

    def _check_stopped(self, tr: "_Track", tid: int,
                       now: float) -> list[TrajectoryEvent]:
        # Stationary (within stopped_eps of the anchor) for >= stopped_seconds.
        if tr.anchor is None or tr.stopped_fired:
            return []
        if now - tr.last_moved >= self._stopped_seconds:
            tr.stopped_fired = True
            return [TrajectoryEvent(EventType.STOPPED, tid, "stopped", {})]
        return []

    def _check_uturn(self, tr: "_Track", tid: int) -> list[TrajectoryEvent]:
        if len(tr.points) < self._uturn_window:
            return []
        pts = [p for p, _ in list(tr.points)[-self._uturn_window:]]
        mid = len(pts) // 2
        v1 = (pts[mid][0] - pts[0][0], pts[mid][1] - pts[0][1])
        v2 = (pts[-1][0] - pts[mid][0], pts[-1][1] - pts[mid][1])
        m1 = math.hypot(*v1)
        m2 = math.hypot(*v2)
        # both legs must be genuine movement, not jitter on a near-stationary track
        if m1 < self._uturn_min_disp or m2 < self._uturn_min_disp:
            tr.uturn_fired = False
            return []
        cos = (v1[0] * v2[0] + v1[1] * v2[1]) / (m1 * m2)
        if cos > 0.5:
            tr.uturn_fired = False
        # a real U-turn goes out and comes back: the net start->end distance must be small
        # relative to the path travelled (otherwise it is just a turn / a curve).
        net = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        came_back = net < 0.5 * (m1 + m2)
        if cos <= math.cos(math.radians(self._uturn_angle)) and came_back and not tr.uturn_fired:
            tr.uturn_fired = True
            return [TrajectoryEvent(EventType.U_TURN, tid, "u-turn", {})]
        return []

    def _expire_tracks(self, now: float) -> None:
        stale = [tid for tid, tr in self._tracks.items()
                 if now - tr.last_seen >= self._expire]
        for tid in stale:
            del self._tracks[tid]
