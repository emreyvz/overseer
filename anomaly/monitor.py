"""Unsupervised per-event-type rate anomaly detection. Pure, thread-safe.

Learns a rolling baseline (EMA of count and count^2 per fixed-size window) for
each event type name and flags a window whose count is a statistical outlier
(mean + k*std) as an ANOMALY event. No Qt, no bus: `record()` is called from
worker threads via a bus callback, `tick()`/`reset()` from the UI thread, so
all shared state lives behind a single lock.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from core.config import Config
from events.types import EventType


@dataclass
class AnomalyEvent:
    event_type: EventType
    label: str
    metadata: dict = field(default_factory=dict)


class AnomalyMonitor:
    def __init__(self, config: Config) -> None:
        self._window_seconds = float(config.get("anomaly.window_seconds", 60.0))
        self._k = float(config.get("anomaly.k", 3.0))
        self._warmup = int(config.get("anomaly.warmup_windows", 8))
        self._min_count = int(config.get("anomaly.min_count", 5))
        self._alpha = float(config.get("anomaly.alpha", 0.15))
        self._lock = threading.Lock()
        self._counts: dict[str, int] = {}
        self._mean: dict[str, float] = {}
        self._sq: dict[str, float] = {}
        self._seen: dict[str, int] = {}

    def record(self, type_name: str, now: float) -> None:
        with self._lock:
            self._counts[type_name] = self._counts.get(type_name, 0) + 1

    def tick(self, now: float) -> list[AnomalyEvent]:
        with self._lock:
            events: list[AnomalyEvent] = []
            for type_name, count in self._counts.items():
                mean = self._mean.get(type_name, 0.0)
                var = max(0.0, self._sq.get(type_name, 0.0) - mean * mean)
                std = var ** 0.5
                if (self._seen.get(type_name, 0) >= self._warmup
                        and count >= self._min_count
                        and count > mean + self._k * std):
                    events.append(AnomalyEvent(
                        EventType.ANOMALY,
                        label=f"Unusual activity: {type_name}",
                        metadata={"type": type_name, "count": count,
                                 "expected": round(mean, 1)},
                    ))
                self._mean[type_name] = (1 - self._alpha) * mean + self._alpha * count
                self._sq[type_name] = ((1 - self._alpha) * self._sq.get(type_name, 0.0)
                                       + self._alpha * count * count)
                self._seen[type_name] = self._seen.get(type_name, 0) + 1
            self._counts.clear()
            return events

    def reset(self) -> None:
        with self._lock:
            self._counts.clear()
            self._mean.clear()
            self._sq.clear()
            self._seen.clear()
