"""Per-source ring buffer of recent frames — the "live but stable" substrate.

The analysis loop pushes each analysed frame in; a query reads the last N seconds. No
persistence, no cross-time index. Thread-safe: written from the worker thread, read from
the request thread.

Note: frames are stored by reference. Callers must pass a frame they will not mutate in
place afterwards (the analysis pipeline hands us a fresh decoded frame per tick)."""
from __future__ import annotations

import threading
from collections import deque

import numpy as np


class RollingFrameStore:
    def __init__(self, window_seconds: float = 3.0, max_frames: int = 30) -> None:
        self._win = float(window_seconds)
        self._max = int(max_frames)
        self._store: dict[int, deque[tuple[float, np.ndarray]]] = {}
        self._lock = threading.Lock()

    def add(self, source_id: int, frame: np.ndarray, now: float) -> None:
        if frame is None or getattr(frame, "size", 0) == 0:
            return
        with self._lock:
            dq = self._store.get(source_id)
            if dq is None:
                dq = deque(maxlen=self._max)
                self._store[source_id] = dq
            dq.append((float(now), frame))

    def window(self, source_id: int, now: float) -> list[np.ndarray]:
        """Frames from the last ``window_seconds``, oldest first."""
        with self._lock:
            dq = self._store.get(source_id)
            if not dq:
                return []
            return [f for (t, f) in dq if now - t <= self._win]

    def sources(self) -> list[int]:
        with self._lock:
            return list(self._store.keys())

    def clear(self, source_id: int | None = None) -> None:
        with self._lock:
            if source_id is None:
                self._store.clear()
            else:
                self._store.pop(source_id, None)
