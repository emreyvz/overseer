"""Offline English natural-language summaries of events. Pure, deterministic."""
from __future__ import annotations

from collections import Counter
from typing import Callable

from events.types import TYPE_NAMES, Event

SourceNameFn = Callable[[int | None], str]

# Higher = more notable; used only to pick the "most notable" incident event.
EVENT_SEVERITY_HINT: dict[str, int] = {
    "ABANDONED_OBJECT": 3, "RESTRICTED": 3, "FIGHTING": 3, "FALLING": 3,
    "REMOVED_OBJECT": 2, "CROWDING": 2, "LOITERING": 2, "WRONG_DIRECTION": 2,
    "RUNNING": 1, "STOPPED": 1, "U_TURN": 1, "TAILGATING": 1, "QUEUE": 1,
    "LINE_CROSS": 1,
}


def _human_duration(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s} s"
    m = s // 60
    if m < 60:
        return f"{m} min"
    h, rem = divmod(m, 60)
    return f"{h} h {rem} min" if rem else f"{h} h"


class EventSummarizer:
    def summarize_event(self, event: Event, source_name_fn: SourceNameFn) -> str:
        meta = event.metadata or {}
        cam = source_name_fn(event.source_id)
        zone = meta.get("zone_name")
        name = event.type.name
        if name == "ABANDONED_OBJECT":
            label = meta.get("label", "object")
            where = zone or cam
            duration = meta.get("duration")
            tail = f", stationary {_human_duration(duration)}" if duration else ""
            return f"Abandoned object: {label} at {where}{tail}. Review advised."
        if name == "RESTRICTED":
            return f"Restricted zone breach: person detected at {zone or cam} ({cam})."
        if name == "CROWDING":
            return f"Crowd: {int(meta.get('count', 0))} people ({cam})."
        if name == "LOITERING":
            return f"Loitering: person lingering at {zone or cam} ({cam})."
        if name == "STOPPED":
            duration = meta.get("duration")
            tail = f", {_human_duration(duration)}" if duration else ""
            return f"Stationary target: {cam}{tail}."
        label = TYPE_NAMES.get(name, name)
        return f"{label}: {zone} ({cam})." if zone else f"{label} ({cam})."

    def summarize_incident(self, events: list[Event], now: float, window_s: float,
                           source_name_fn: SourceNameFn) -> str:
        recent = [e for e in events if now - e.timestamp <= window_s]
        window_min = int(window_s // 60)
        if not recent:
            return f"No events in the last {window_min} minutes."
        counts = Counter(e.type.name for e in recent)
        parts = sorted(counts.items(), key=lambda kv: (-kv[1], TYPE_NAMES.get(kv[0], kv[0])))
        listing = ", ".join(f"{TYPE_NAMES.get(n, n)} ×{c}" for n, c in parts)
        notable = max(recent,
                      key=lambda e: (EVENT_SEVERITY_HINT.get(e.type.name, 0), e.timestamp))
        return (f"{len(recent)} events in the last {window_min} minutes: {listing}. "
                f"Most notable: {self.summarize_event(notable, source_name_fn)}")
