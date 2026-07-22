"""Threaded timelapse writer: time-sampled frames into a timelapse video."""
from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path

import cv2
import numpy as np
from loguru import logger

from camera.frame_buffer import Frame
from core.config import Config
from storage.database import Database
from storage.video_writer import open_video_writer


class TimelapseWriter(threading.Thread):
    def __init__(self, config: Config, db: Database) -> None:
        super().__init__(daemon=True, name="TimelapseWriter")
        self._db = db
        self._enabled = bool(config.get("timelapse.enabled", True))
        self._dir = Path(str(config.get("timelapse.dir", "data/timelapse")))
        self._interval = float(config.get("timelapse.sample_interval_seconds", 10.0))
        self._fps = float(config.get("timelapse.fps", 30.0))
        # Rotate the open timelapse segment after this long so retention can
        # see and prune it; otherwise a single timelapse can grow unbounded
        # and stays invisible to retention until it's finally finalized.
        self._max_segment_seconds = float(
            config.get("timelapse.max_segment_seconds", 21600.0)
        )
        self._codecs = list(config.get("recording.codecs", ["mp4v", "XVID", "MJPG"]))
        self._lock = threading.Lock()
        self._queue: deque[np.ndarray] = deque()
        self._queue_max = 120
        self._last_sample = 0.0
        self._flush_requested = False
        self._stop_event = threading.Event()
        self._writer: cv2.VideoWriter | None = None
        self._writer_path: Path | None = None
        self._start_ts = 0.0
        self._size: tuple[int, int] | None = None
        self.source_id: int | None = None
        # Captured from self.source_id at the moment a writer is opened, so
        # a finalize always attributes the clip to the source whose SAMPLES
        # were actually written, not whatever source_id happens to be
        # current when flush/finalize runs (e.g. after a source switch).
        self._pending_source_id: int | None = None
        # Monotonic per-instance counter appended to segment filenames so two
        # opens within the same wall-clock second (the %Y%m%d_%H%M%S stamp's
        # resolution -- e.g. right after a max_segment_seconds rotation)
        # don't reuse a name and silently overwrite the prior file.
        self._segment_seq = 0

    def offer(self, frame: Frame) -> None:
        if not self._enabled:
            return
        now = time.time()
        with self._lock:
            if now - self._last_sample < self._interval:
                return
            self._last_sample = now
            self._queue.append(frame.image.copy())
            while len(self._queue) > self._queue_max:
                self._queue.popleft()

    def flush_now(self) -> None:
        with self._lock:
            self._flush_requested = True

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        while not self._stop_event.is_set():
            image = self._pop()
            if image is not None:
                self._write(image)
            now = time.time()
            if self._take_flush():
                self._finalize(now)
            elif self._should_rotate(now):
                # Rotate on a max-segment interval regardless of flush
                # requests, so a long-running timelapse writes a recordings
                # row periodically instead of staying invisible to retention
                # (and growing unbounded) until an explicit flush or stop().
                self._finalize(now)
            if image is None:
                time.sleep(0.02)
        # Drain any samples still buffered in the queue before finalizing,
        # so accepted-but-unpopped frames are not silently dropped on stop().
        while True:
            image = self._pop()
            if image is None:
                break
            self._write(image)
        self._finalize(time.time())

    def _pop(self) -> np.ndarray | None:
        with self._lock:
            return self._queue.popleft() if self._queue else None

    def _take_flush(self) -> bool:
        with self._lock:
            if self._flush_requested:
                self._flush_requested = False
                return True
            return False

    def _should_rotate(self, now: float) -> bool:
        return self._writer is not None and now - self._start_ts >= self._max_segment_seconds

    def _write(self, image: np.ndarray) -> None:
        if not self._enabled:
            return
        if self._writer is None:
            height, width = image.shape[:2]
            self._size = (width, height)
            self._segment_seq += 1
            stamp = time.strftime("%Y%m%d_%H%M%S")
            try:
                result = open_video_writer(
                    self._dir / f"timelapse_{stamp}_{self._segment_seq:04d}",
                    self._fps, self._size, self._codecs,
                )
            except Exception:
                logger.exception("timelapse disabled: video writer raised while opening")
                self._enabled = False
                return
            if result is None:
                logger.error("timelapse disabled: no codec")
                self._enabled = False
                return
            self._writer, self._writer_path = result
            self._start_ts = time.time()
            self._pending_source_id = self.source_id
        if (image.shape[1], image.shape[0]) != self._size:
            image = cv2.resize(image, self._size)
        try:
            self._writer.write(image)
        except Exception:
            logger.exception("timelapse write failed")

    def _finalize(self, now: float) -> None:
        if self._writer is None or self._writer_path is None:
            return
        self._writer.release()
        path = self._writer_path
        self._writer = None
        self._writer_path = None
        size = path.stat().st_size if path.exists() else 0
        try:
            self._db.add_recording(
                kind="timelapse", path=str(path), start_ts=self._start_ts,
                end_ts=now, mode="timelapse", trigger=None,
                source_id=self._pending_source_id, size_bytes=size,
            )
        except Exception:
            logger.exception("failed to record timelapse metadata")
