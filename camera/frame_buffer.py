"""Bounded frame queue with drop-oldest backpressure."""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class Frame:
    image: np.ndarray
    timestamp: float
    seq: int


class FrameBuffer:
    def __init__(self, maxsize: int = 5) -> None:
        self._maxsize = maxsize
        self._queue: deque[Frame] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self.dropped = 0
        # Optional display tap: called (off the lock) with EVERY captured frame, at full camera rate,
        # so the live stream can be served at capture rate instead of the slower analysis rate.
        self.on_put = None

    def put(self, frame: Frame) -> bool:
        with self._lock:
            accepted = True
            if len(self._queue) >= self._maxsize:
                self._queue.popleft()
                self.dropped += 1
                accepted = False
            self._queue.append(frame)
            self._not_empty.notify()
        cb = self.on_put
        if cb is not None:
            try:
                cb(frame)
            except Exception:  # noqa: BLE001 - a display-tap error must never break capture
                pass
        return accepted

    def get(self, timeout: float | None = None) -> Frame | None:
        with self._not_empty:
            if not self._queue:
                self._not_empty.wait(timeout)
            if not self._queue:
                return None
            return self._queue.popleft()

    def qsize(self) -> int:
        with self._lock:
            return len(self._queue)

    def clear(self) -> None:
        with self._lock:
            self._queue.clear()
