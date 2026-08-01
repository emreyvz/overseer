"""Track-aware zone membership/dwell/crossing monitor. Runs on the analysis thread."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from core.config import Config
from events.types import EventType
from plugins.base import Detection
from storage.database import Zone
from zones.model import ZoneView, point_in_polygon, segment_intersects, side_sign


@dataclass
class ZoneEvent:
    event_type: EventType
    zone_id: int
    zone_name: str
    track_id: int
    label: str
    metadata: dict = field(default_factory=dict)


@dataclass
class _ZoneTrackState:
    inside: bool = False
    entered_ts: float = 0.0
    loiter_fired: bool = False


def _bottom_center(bbox: tuple[int, int, int, int]) -> tuple[int, int]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) // 2, y2)


class ZoneMonitor:
    def __init__(self, config: Config) -> None:
        self._default_loiter = float(config.get("zones.loiter_seconds", 30.0))
        self._expire = float(config.get("zones.expire_seconds", 5.0))
        self._zones: list[Zone] = []
        self._state: dict[tuple[int, int], _ZoneTrackState] = {}
        self._prev_point: dict[int, tuple[int, int]] = {}
        self._last_seen: dict[int, float] = {}
        self._entries: dict[int, int] = {}
        self._exits: dict[int, int] = {}
        self._tailgate_seconds = float(config.get("zones.tailgate_seconds", 2.0))
        self._queue_min = int(config.get("zones.queue_min", 3))
        self._gate_last: dict[int, tuple[int, float]] = {}
        self._queue_fired: dict[int, bool] = {}
        # Per-(zone, track, event) refire cooldown: a foot-point jittering across a zone boundary
        # or tripwire used to re-fire RESTRICTED / LINE_CROSS every frame, inflating the counts the
        # suggestions engine reports. Debounce so one crossing is one event.
        self._refire = float(config.get("zones.refire_seconds", 3.0))
        self._fired_at: dict[tuple[int, int, str], float] = {}
        # set_zones()/reset() are called from the UI thread (drawing/deleting
        # zones, connecting a source) while process()/occupancy()/counts()/
        # snapshot() run on the analysis worker thread; without this lock a
        # reset() clearing the state dicts can land mid-iteration inside
        # process() and raise "dictionary changed size during iteration".
        # RLock so set_zones() -> reset() and snapshot() -> occupancy() (both
        # calling another locked method while already holding the lock) work.
        self._lock = threading.RLock()

    def set_zones(self, zones: list[Zone]) -> None:
        with self._lock:
            self._zones = list(zones)
            self.reset()

    def reset(self) -> None:
        with self._lock:
            self._state.clear()
            self._prev_point.clear()
            self._last_seen.clear()
            self._entries.clear()
            self._exits.clear()
            self._gate_last.clear()
            self._queue_fired.clear()
            self._fired_at.clear()

    def _loiter_for(self, zone: Zone) -> float:
        return zone.loiter_seconds if zone.loiter_seconds is not None \
            else self._default_loiter

    def process(self, detections: list[Detection], now: float,
                source_id: int | None) -> list[ZoneEvent]:
        with self._lock:
            events: list[ZoneEvent] = []
            for det in detections:
                if det.track_id is None or det.category not in ("person", "vehicle"):
                    continue
                tid = det.track_id
                pt = _bottom_center(det.bbox)
                self._last_seen[tid] = now
                for zone in self._zones:
                    if zone.type == "line":
                        events += self._handle_line(zone, tid, pt, now)
                    else:
                        events += self._handle_polygon(zone, tid, pt, now)
                self._prev_point[tid] = pt
            self._expire_tracks(now)
            events += self._check_queues()
            return events

    def _handle_polygon(self, zone: Zone, tid: int, pt: tuple[int, int],
                        now: float) -> list[ZoneEvent]:
        events: list[ZoneEvent] = []
        key = (zone.id, tid)
        st = self._state.get(key)
        if st is None:
            st = _ZoneTrackState()
            self._state[key] = st
        inside = point_in_polygon(pt, zone.polygon)
        if inside and not st.inside:
            st.inside = True
            st.entered_ts = now
            st.loiter_fired = False
            self._entries[zone.id] = self._entries.get(zone.id, 0) + 1
            if zone.type == "restricted":
                events += self._fire(EventType.RESTRICTED, zone, tid, now)
        elif inside and st.inside:
            if not st.loiter_fired and now - st.entered_ts >= self._loiter_for(zone):
                st.loiter_fired = True
                events.append(self._event(EventType.LOITERING, zone, tid))
        elif not inside and st.inside:
            st.inside = False
            self._exits[zone.id] = self._exits.get(zone.id, 0) + 1
        return events

    def _handle_line(self, zone: Zone, tid: int, pt: tuple[int, int],
                     now: float) -> list[ZoneEvent]:
        prev = self._prev_point.get(tid)
        if prev is None or len(zone.polygon) < 2:
            return []
        a, b = zone.polygon[0], zone.polygon[1]
        if not segment_intersects(prev, pt, a, b):
            return []
        direction = "a->b" if side_sign(a, b, prev) < 0 <= side_sign(a, b, pt) \
            else "b->a"
        events: list[ZoneEvent] = []
        allowed = zone.allowed_direction
        if allowed is not None and direction != allowed:
            events += self._fire(EventType.WRONG_DIRECTION, zone, tid, now,
                                 {"direction": direction})
        else:
            events += self._fire(EventType.LINE_CROSS, zone, tid, now,
                                 {"direction": direction})
        last = self._gate_last.get(zone.id)
        if last is not None and last[0] != tid and now - last[1] <= self._tailgate_seconds:
            events.append(self._event(EventType.TAILGATING, zone, tid,
                                      {"prev_track": last[0]}))
        self._gate_last[zone.id] = (tid, now)
        return events

    def _event(self, etype: EventType, zone: Zone, tid: int,
               extra: dict | None = None) -> ZoneEvent:
        meta = {"zone_id": zone.id, "zone_name": zone.name, "track_id": tid}
        if extra:
            meta.update(extra)
        return ZoneEvent(event_type=etype, zone_id=zone.id, zone_name=zone.name,
                         track_id=tid, label=zone.name, metadata=meta)

    def _fire(self, etype: EventType, zone: Zone, tid: int, now: float,
              extra: dict | None = None) -> list[ZoneEvent]:
        """Emit an event unless the same (zone, track, type) fired within the refire cooldown —
        so boundary/tripwire jitter counts as one event, not one per frame."""
        k = (zone.id, tid, etype.name)
        last = self._fired_at.get(k)
        if last is not None and now - last < self._refire:
            return []
        self._fired_at[k] = now
        return [self._event(etype, zone, tid, extra)]

    def _check_queues(self) -> list[ZoneEvent]:
        events: list[ZoneEvent] = []
        occ = self.occupancy()
        for zone in self._zones:
            if zone.type != "queue":
                continue
            count = occ.get(zone.id, 0)
            if count >= self._queue_min and not self._queue_fired.get(zone.id):
                self._queue_fired[zone.id] = True
                events.append(self._event(EventType.QUEUE, zone, 0,
                                          {"length": count}))
            elif count < self._queue_min:
                self._queue_fired[zone.id] = False
        return events

    def _expire_tracks(self, now: float) -> None:
        stale = [tid for tid, seen in self._last_seen.items()
                 if now - seen >= self._expire]
        for tid in stale:
            del self._last_seen[tid]
            self._prev_point.pop(tid, None)
            for key in [k for k in self._state if k[1] == tid]:
                if self._state[key].inside:
                    self._exits[key[0]] = self._exits.get(key[0], 0) + 1
                del self._state[key]
            for k in [fk for fk in self._fired_at if fk[1] == tid]:
                del self._fired_at[k]

    def occupancy(self) -> dict[int, int]:
        with self._lock:
            counts: dict[int, int] = {z.id: 0 for z in self._zones}
            for (zone_id, _tid), st in self._state.items():
                if st.inside:
                    counts[zone_id] = counts.get(zone_id, 0) + 1
            return counts

    def counts(self) -> dict[int, tuple[int, int]]:
        with self._lock:
            return {z.id: (self._entries.get(z.id, 0), self._exits.get(z.id, 0))
                    for z in self._zones}

    def snapshot(self) -> list[ZoneView]:
        with self._lock:
            occ = self.occupancy()
            return [ZoneView(zone_id=z.id, name=z.name, type=z.type, polygon=z.polygon,
                             occupancy=occ.get(z.id, 0),
                             entries=self._entries.get(z.id, 0),
                             exits=self._exits.get(z.id, 0),
                             allowed_direction=z.allowed_direction) for z in self._zones]
