"""Live per-track licence-plate reading for the tracking overlay.

ANPR is expensive, so it never runs inline in the analysis loop. Vehicle crops are OFFERED
here (throttled per track), read on a background thread, and voted across several frames so
a single blurry misread can't stick. The tracking card shows the voted plate as an estimate.
When EasyOCR isn't installed the reader is simply unavailable and no plates are produced.
"""
from __future__ import annotations

import threading
from collections import deque

from match.anpr.normalize import normalize_plate
from match.anpr.voting import PlateVoter


class LivePlateReader:
    def __init__(self, reader=None, *, min_agreement: int = 2, fold_confusable: bool = False,
                 interval: float = 2.5, min_w: int = 60, min_h: int = 20,
                 max_tracks: int = 96, history: int = 12) -> None:
        # reader: callable(crop)->[(text,conf)] with .available(); default = lazy EasyOCR
        self._reader = reader
        self._voter = PlateVoter(min_agreement=min_agreement)
        self._fold = fold_confusable
        self._interval = float(interval)
        self._min_w, self._min_h = int(min_w), int(min_h)
        self._max_tracks, self._history = int(max_tracks), int(history)
        self._reads: dict[str, list[tuple[str, float]]] = {}
        self._voted: dict[str, tuple[str, float]] = {}
        self._last_offer: dict[str, float] = {}
        self._queue: deque[tuple[str, object]] = deque()
        # keep the last few distinct crops per track so feature 7 can fuse them into a super-res
        # plate (the crops were previously read once and thrown away).
        self._recent: dict[str, deque] = {}
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="LivePlateReader", daemon=True)
        self._thread.start()

    def _get_reader(self):
        if self._reader is None:
            from .plate_ocr import default_plate_reader
            self._reader = default_plate_reader()
        return self._reader

    def offer(self, track_id: str, crop, now: float) -> None:
        """Queue a vehicle crop for reading — throttled per track, skipping tiny crops
        (plates need resolution). Cheap and non-blocking; the OCR happens off-thread."""
        if crop is None or getattr(crop, "size", 0) == 0:
            return
        if crop.shape[1] < self._min_w or crop.shape[0] < self._min_h:
            return
        with self._cv:
            if now - self._last_offer.get(track_id, 0.0) < self._interval:
                return
            self._last_offer[track_id] = now
            self._queue.append((track_id, crop))
            ring = self._recent.get(track_id)
            if ring is None:
                if len(self._recent) >= self._max_tracks:            # bound memory: drop an old ring
                    self._recent.pop(next(iter(self._recent)), None)
                ring = self._recent[track_id] = deque(maxlen=10)
            ring.append(crop.copy())
            self._cv.notify()

    def recent_crops(self, track_id: str) -> list:
        """The buffered distinct crops of one vehicle track, newest last (for super-res fusion)."""
        with self._lock:
            return list(self._recent.get(track_id, ()))

    def process_one(self) -> bool:
        """Read one queued crop and fold it into that track's vote. Returns True if it did
        work. Split out from the loop so tests can drive it synchronously."""
        with self._cv:
            if not self._queue:
                return False
            track_id, crop = self._queue.popleft()
        reader = self._get_reader()
        if reader is None or not reader.available():
            return False
        try:
            reads = list(reader(crop) or [])
        except Exception:  # noqa: BLE001 - OCR failure is just "no reads"
            reads = []
        if not reads:
            return True
        with self._lock:
            lst = self._reads.setdefault(track_id, [])
            lst.extend((str(t), float(c)) for t, c in reads)
            if len(lst) > self._history:
                del lst[: len(lst) - self._history]
            plate, conf = self._voter.vote(lst, fold_confusable=self._fold)
            if plate:
                self._voted[track_id] = (plate, round(conf, 3))
            self._evict_locked()
        return True

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._cv:
                while not self._queue and not self._stop.is_set():
                    self._cv.wait(0.5)
            if self._stop.is_set():
                return
            self.process_one()

    def plate_for(self, track_id: str) -> tuple[str, float] | None:
        with self._lock:
            return self._voted.get(track_id)

    def read(self, crop) -> list[tuple[str, float]]:
        """One-shot OCR of a crop (for on-demand plate search). Returns [] if unavailable."""
        reader = self._get_reader()
        if reader is None or not reader.available() or crop is None or getattr(crop, "size", 0) == 0:
            return []
        try:
            return [(str(t), float(c)) for t, c in (reader(crop) or [])]
        except Exception:  # noqa: BLE001
            return []

    def prune(self, alive_ids: set[str]) -> None:
        """Drop state for tracks that are no longer on screen."""
        with self._lock:
            for store in (self._reads, self._voted, self._last_offer):
                for k in [k for k in store if k not in alive_ids]:
                    del store[k]

    def _evict_locked(self) -> None:
        if len(self._voted) > self._max_tracks:
            for k in list(self._voted)[: len(self._voted) - self._max_tracks]:
                self._voted.pop(k, None)
                self._reads.pop(k, None)

    def available(self) -> bool:
        reader = self._get_reader()
        return bool(reader and reader.available())

    def normalize(self, text: str) -> str:
        return normalize_plate(text, self._fold)

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
