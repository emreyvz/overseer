"""Accessory-persistence detection: abandoned / removed object. Worker-thread only."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import dist

from core.config import Config
from events.types import EventType
from plugins.base import Detection


@dataclass
class ObjectEvent:
    event_type: EventType
    track_id: int
    label: str
    metadata: dict = field(default_factory=dict)


@dataclass
class _Obj:
    points: deque
    first_seen: float
    last_seen: float
    label: str
    established: bool = False
    owner_frames: int = 0          # frames a person was genuinely near it (it's someone's)
    owner_last_seen: float = 0.0   # last time a person was within owner_distance
    abandoned_fired: bool = False
    removed_fired: bool = False


def _center(bbox: tuple[int, int, int, int]) -> tuple[int, int]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) // 2, (y1 + y2) // 2)


class ObjectMonitor:
    def __init__(self, config: Config) -> None:
        self._buffer_size = int(config.get("objects.buffer_size", 300))
        self._expire = float(config.get("objects.expire_seconds", 30.0))
        self._abandon = float(config.get("objects.abandon_seconds", 10.0))
        self._eps = float(config.get("objects.obj_eps", 20.0))
        self._owner_distance = float(config.get("objects.owner_distance", 120.0))
        self._stable = float(config.get("objects.stable_seconds", 3.0))
        self._removed = float(config.get("objects.removed_seconds", 5.0))
        # Smart abandonment: only alert on real luggage that a person BROUGHT and then LEFT.
        # Requires the object to have been genuinely someone's (owner near for >= owner_min
        # frames) and then unattended for `unattended_seconds` — so permanently-static
        # fixtures (a bag on a rack, a parked stroller) never trigger.
        self._unattended = float(config.get("objects.unattended_seconds", 8.0))
        self._owner_min_frames = int(config.get("objects.owner_min_frames", 3))
        classes = config.get("objects.abandon_classes",
                             ["backpack", "handbag", "suitcase"])
        self._abandon_classes = {str(c).lower() for c in classes} if classes else None
        self._objs: dict[int, _Obj] = {}

    def reset(self) -> None:
        self._objs.clear()

    def process(self, accessories: list[Detection], persons: list[Detection],
                now: float) -> list[ObjectEvent]:
        events: list[ObjectEvent] = []
        person_centers = [_center(p.bbox) for p in persons if p.track_id is not None]
        seen: set[int] = set()
        for det in accessories:
            if det.track_id is None:
                continue
            seen.add(det.track_id)
            events += self._update_object(det, now, person_centers)
        events += self._check_removed(seen, now)
        return events

    def _update_object(self, det: Detection, now: float,
                       person_centers: list[tuple[int, int]]) -> list[ObjectEvent]:
        tid = det.track_id
        cx, cy = _center(det.bbox)
        obj = self._objs.get(tid)
        if obj is None:
            obj = _Obj(points=deque(maxlen=self._buffer_size), first_seen=now,
                       last_seen=now, label=det.label)
            self._objs[tid] = obj
        obj.points.append((cx, cy, now))
        obj.last_seen = now
        # owner association: a person actually near it (so a fixture that no one ever tends
        # is never mistaken for abandoned luggage)
        if any(dist((cx, cy), pc) <= self._owner_distance for pc in person_centers):
            obj.owner_frames += 1
            obj.owner_last_seen = now
        if now - obj.first_seen >= self._stable:
            obj.established = True
        if obj.abandoned_fired or not obj.established:
            return []
        # only real luggage classes (configurable)
        if self._abandon_classes is not None and det.label.lower() not in self._abandon_classes:
            return []
        # stationary over the recent window
        window = [(x, y, t) for (x, y, t) in obj.points if now - t <= self._abandon]
        if len(window) < 2:
            return []
        xs = [x for x, _, _ in window]
        ys = [y for _, y, _ in window]
        if max(max(xs) - min(xs), max(ys) - min(ys)) >= self._eps:
            return []                                   # moving -> not abandoned
        # the abandonment signature: it was genuinely someone's, and they've now been gone
        # long enough that it is unattended
        if obj.owner_frames < self._owner_min_frames:
            return []                                   # never really anyone's -> a fixture
        if now - obj.owner_last_seen < self._unattended:
            return []                                   # owner still around / only just left
        obj.abandoned_fired = True
        return [ObjectEvent(EventType.ABANDONED_OBJECT, tid, obj.label,
                            {"label": obj.label, "pos": [cx, cy]})]

    def _check_removed(self, seen: set[int], now: float) -> list[ObjectEvent]:
        events: list[ObjectEvent] = []
        for tid in list(self._objs):
            obj = self._objs[tid]
            if tid in seen:
                continue
            gone = now - obj.last_seen
            if obj.established and gone >= self._removed and not obj.removed_fired:
                obj.removed_fired = True
                events.append(ObjectEvent(EventType.REMOVED_OBJECT, tid, obj.label,
                                          {"label": obj.label}))
                del self._objs[tid]
            elif gone >= self._expire:
                del self._objs[tid]
        return events
