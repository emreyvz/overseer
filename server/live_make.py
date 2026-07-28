"""Live per-track vehicle make (brand) classification for the tracking overlay.

Make classification is CPU-bound (~150 ms per crop), so — exactly like ANPR — it never runs
inline in the analysis loop. Vehicle crops are OFFERED here (throttled per track), classified
on a background thread, and the last confident brand is cached so the tracking card can read
it instantly. When the classifier's weights are absent the reader is simply idle and no make
is produced.
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any


class LiveMakeReader:
    def __init__(self, classifier: Any, *, interval: float = 4.0, min_w: int = 64,
                 min_h: int = 48, max_tracks: int = 96) -> None:
        self._clf = classifier
        self._interval = float(interval)
        self._min_w, self._min_h = int(min_w), int(min_h)
        self._max_tracks = int(max_tracks)
        self._make: dict[str, str] = {}            # track_id -> last confident brand
        self._last_offer: dict[str, float] = {}
        self._queue: deque[tuple[str, Any]] = deque()
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="LiveMakeReader", daemon=True)
        self._thread.start()

    def offer(self, track_id: str, crop: Any, now: float) -> None:
        """Queue a vehicle crop for classification — throttled per track, skipping crops too
        small to read a brand from. Cheap and non-blocking; the ViT runs off-thread."""
        if crop is None or getattr(crop, "size", 0) == 0:
            return
        if crop.shape[1] < self._min_w or crop.shape[0] < self._min_h:
            return
        with self._cv:
            if now - self._last_offer.get(track_id, float("-inf")) < self._interval:
                return          # -inf default: a track's first crop is never throttled
            self._last_offer[track_id] = now
            self._queue.append((track_id, crop))
            self._cv.notify()

    def make_for(self, track_id: str) -> str | None:
        with self._lock:
            return self._make.get(track_id)

    def process_one(self) -> bool:
        """Classify one queued crop and cache its brand. Returns True if it did work. Split
        out from the loop so tests can drive it synchronously."""
        with self._cv:
            if not self._queue:
                return False
            track_id, crop = self._queue.popleft()
        hit = None
        try:
            hit = self._clf.classify(crop)
        except Exception:  # noqa: BLE001
            hit = None
        if hit:
            with self._lock:
                self._make[track_id] = hit[0]
                if len(self._make) > self._max_tracks:
                    for k in list(self._make)[: len(self._make) - self._max_tracks]:
                        self._make.pop(k, None)
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                while not self._queue and not self._stop.is_set():
                    self._cv.wait(timeout=1.0)
            if self._stop.is_set():
                break
            self.process_one()

    def prune(self, alive_ids: set[str]) -> None:
        with self._lock:
            for store in (self._make, self._last_offer):
                for k in [k for k in store if k not in alive_ids]:
                    del store[k]

    def reset(self) -> None:
        with self._lock:
            self._make.clear()
            self._last_offer.clear()
        with self._cv:
            self._queue.clear()

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
