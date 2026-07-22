"""Per-track pose/crowd/fight detection from person detections. Worker-thread only."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

from core.config import Config
from events.types import EventType
from plugins.base import Detection


@dataclass
class PoseEvent:
    event_type: EventType
    track_id: int
    label: str
    metadata: dict = field(default_factory=dict)


@dataclass
class _PTrack:
    points: deque
    was_upright: bool = False
    falling_fired: bool = False
    last_seen: float = 0.0


class PoseMonitor:
    def __init__(self, config: Config) -> None:
        self._buffer_size = int(config.get("pose.buffer_size", 30))
        self._expire = float(config.get("pose.expire_seconds", 5.0))
        self._standing = float(config.get("pose.standing_aspect", 1.2))
        self._fallen = float(config.get("pose.fallen_aspect", 0.8))
        self._crowd_min = int(config.get("pose.crowd_min", 8))
        self._fight_distance = float(config.get("pose.fight_distance", 80.0))
        self._fight_motion = float(config.get("pose.fight_motion", 120.0))
        self._tracks: dict[int, _PTrack] = {}
        self._crowd_fired = False
        self._fight_fired = False

    def reset(self) -> None:
        self._tracks.clear()
        self._crowd_fired = False
        self._fight_fired = False

    def process(self, persons: list[Detection], now: float) -> list[PoseEvent]:
        events: list[PoseEvent] = []
        for det in persons:
            if det.track_id is None:
                continue
            events += self._update_track(det, now)
        events += self._check_crowd(len(persons))
        events += self._check_fight(now)
        self._expire_tracks(now)
        return events

    def _update_track(self, det: Detection, now: float) -> list[PoseEvent]:
        tid = det.track_id
        x1, y1, x2, y2 = det.bbox
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        tr = self._tracks.get(tid)
        if tr is None:
            tr = _PTrack(points=deque(maxlen=self._buffer_size))
            self._tracks[tid] = tr
        tr.points.append((w, h, cx, cy, now))
        tr.last_seen = now
        aspect = h / w
        if aspect >= self._standing:
            tr.was_upright = True
            tr.falling_fired = False
        if tr.was_upright and aspect <= self._fallen and not tr.falling_fired:
            tr.falling_fired = True
            return [PoseEvent(EventType.FALLING, tid, "fall",
                              {"aspect": round(aspect, 2)})]
        return []

    def _check_crowd(self, count: int) -> list[PoseEvent]:
        if count < self._crowd_min:
            self._crowd_fired = False
            return []
        if not self._crowd_fired:
            self._crowd_fired = True
            return [PoseEvent(EventType.CROWDING, 0, "crowd", {"count": count})]
        return []

    def _speed(self, tr: "_PTrack") -> float:
        if len(tr.points) < 2:
            return 0.0
        _, _, x0, y0, t0 = tr.points[-2]
        _, _, x1, y1, t1 = tr.points[-1]
        dt = t1 - t0
        if dt <= 0:
            return 0.0
        return math.dist((x0, y0), (x1, y1)) / dt

    def _check_fight(self, now: float) -> list[PoseEvent]:
        active = [tid for tid, tr in self._tracks.items()
                  if tr.last_seen == now and self._speed(tr) >= self._fight_motion]
        pair = None
        for i in range(len(active)):
            for j in range(i + 1, len(active)):
                a = self._tracks[active[i]].points[-1]
                b = self._tracks[active[j]].points[-1]
                if math.dist((a[2], a[3]), (b[2], b[3])) <= self._fight_distance:
                    pair = (active[i], active[j])
                    break
            if pair is not None:
                break
        if pair is None:
            self._fight_fired = False
            return []
        if not self._fight_fired:
            self._fight_fired = True
            return [PoseEvent(EventType.FIGHTING, pair[0], "fight",
                              {"tracks": list(pair)})]
        return []

    def _expire_tracks(self, now: float) -> None:
        stale = [tid for tid, tr in self._tracks.items()
                 if now - tr.last_seen >= self._expire]
        for tid in stale:
            del self._tracks[tid]
