"""Thread-safe publish/subscribe event bus."""
from __future__ import annotations

import threading
from typing import Callable

from loguru import logger

from events.types import Event, EventType

Callback = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subs: list[tuple[EventType | None, Callback]] = []

    def subscribe(self, event_type: EventType | None, callback: Callback) -> Callable[[], None]:
        entry = (event_type, callback)
        with self._lock:
            self._subs.append(entry)

        def unsubscribe() -> None:
            with self._lock:
                if entry in self._subs:
                    self._subs.remove(entry)

        return unsubscribe

    def publish(self, event: Event) -> None:
        with self._lock:
            subs = list(self._subs)
        for etype, callback in subs:
            if etype is not None and etype is not event.type:
                continue
            try:
                callback(event)
            except Exception:
                logger.exception("event callback failed for {}", event.type.name)
