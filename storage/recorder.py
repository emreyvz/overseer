"""Threaded video recorder: continuous/motion/event modes with pre/post-roll."""
from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
from loguru import logger

from camera.frame_buffer import Frame
from core.config import Config
from storage.database import Database
from storage.video_writer import open_video_writer

StatusCallback = Callable[[str], None]


class Recorder(threading.Thread):
    def __init__(self, config: Config, db: Database,
                 on_status: StatusCallback | None = None) -> None:
        super().__init__(daemon=True, name="Recorder")
        self._db = db
        self._on_status = on_status
        self._dir = Path(str(config.get("recording.dir", "data/recordings")))
        self._fps = float(config.get("recording.fps", 15.0))
        self._pre_roll = float(config.get("recording.pre_roll_seconds", 5.0))
        self._post_roll = float(config.get("recording.post_roll_seconds", 5.0))
        self._segment = float(config.get("recording.segment_seconds", 300.0))
        self._max_bytes = int(config.get("recording.max_buffer_mb", 512)) * 1024 * 1024
        self._codecs = list(config.get("recording.codecs", ["mp4v", "XVID"]))
        self._lock = threading.Lock()
        self._mode = str(config.get("recording.mode", "off"))
        self._queue: deque[tuple[np.ndarray, bool, float]] = deque()
        self._queue_max = max(2, int(self._fps * 2))
        self._ring: deque[np.ndarray] = deque()
        self._ring_bytes = 0
        self._stop_event = threading.Event()
        self._close_request = threading.Event()
        self._trigger_at: float | None = None
        self._trigger_label: str | None = None
        self._writer: cv2.VideoWriter | None = None
        self._writer_path: Path | None = None
        self._clip_mode: str | None = None
        self._clip_start = 0.0
        self._clip_trigger: str | None = None
        self._last_active = 0.0
        self._size: tuple[int, int] | None = None
        self.source_id: int | None = None
        # Monotonic per-instance counter appended to clip filenames so two
        # opens within the same wall-clock second (the %Y%m%d_%H%M%S stamp's
        # resolution) don't reuse a name: cv2.VideoWriter would silently
        # truncate/overwrite the earlier file while its DB row keeps
        # pointing at the now-corrupted path.
        self._clip_seq = 0

    # -- public API ----------------------------------------------------------
    def offer(self, frame: Frame, motion_active: bool) -> None:
        with self._lock:
            if self._mode == "off" and self._writer is None:
                return
            self._queue.append((frame.image.copy(), motion_active, frame.timestamp))
            while len(self._queue) > self._queue_max:
                self._queue.popleft()

    def trigger(self, label: str) -> None:
        with self._lock:
            self._trigger_at = time.time()
            self._trigger_label = label

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self._mode = mode

    def current_mode(self) -> str:
        with self._lock:
            return self._mode

    def is_recording(self) -> bool:
        with self._lock:
            return self._writer is not None

    def stop(self) -> None:
        self._stop_event.set()

    def request_close(self) -> None:
        """Ask the run loop to close any open clip and drop buffered frames.

        Used on source switch: the frames already queued/ring-buffered belong
        to the source that's being disconnected, and must not bleed into a
        clip that gets attributed to the next source.
        """
        self._close_request.set()

    # -- thread loop ---------------------------------------------------------
    def run(self) -> None:
        while not self._stop_event.is_set():
            if self._close_request.is_set():
                self._handle_close_request(time.time())
                continue
            item = self._pop()
            if item is None:
                self._maybe_close_idle(time.time())
                time.sleep(0.01)
                continue
            image, motion_active, ts = item
            self._process(image, motion_active, ts)
        self._close_clip(time.time())
        self._status("stopped")

    def _handle_close_request(self, now: float) -> None:
        self._close_clip(now)
        with self._lock:
            self._queue.clear()
            self._ring.clear()
            self._ring_bytes = 0
        self._close_request.clear()

    def _pop(self) -> tuple[np.ndarray, bool, float] | None:
        with self._lock:
            if self._queue:
                return self._queue.popleft()
            return None

    def _push_ring(self, image: np.ndarray) -> None:
        self._ring.append(image)
        self._ring_bytes += image.nbytes
        max_frames = max(1, int(self._pre_roll * self._fps))
        while len(self._ring) > max_frames or self._ring_bytes > self._max_bytes:
            old = self._ring.popleft()
            self._ring_bytes -= old.nbytes

    def _process(self, image: np.ndarray, motion_active: bool, ts: float) -> None:
        mode = self.current_mode()
        now = time.time()
        # Mode changed while a clip was open (e.g. -> "off"): close it now
        # instead of letting it grow unbounded until stop().
        if self._writer is not None and mode != self._clip_mode:
            self._close_clip(now)
        want = self._wants_recording(mode, motion_active, now)
        if want and self._writer is None:
            # Pre-roll flush only applies to motion/event clips; continuous
            # segments must not re-write already-recorded ring frames.
            flush = mode in ("motion", "event")
            self._open_clip(image, mode, now)
            if flush and self._writer is not None:
                self._flush_ring()
        if self._writer is not None:
            self._write(image)
            if motion_active:
                self._last_active = now
            self._maybe_rotate_or_close(mode, now)
        # Push the current frame to the ring AFTER it has been written, so
        # the ring only ever holds strictly past frames for the next flush.
        # Keep the ring fresh across ALL non-off modes (including
        # "continuous"), not just motion/event: otherwise the ring goes
        # stale while continuous is active and a later switch back to
        # motion/event would flush pre-continuous frames as pre-roll. The
        # ring is still only FLUSHED for motion/event opens (see the `flush`
        # gate above), so this only keeps the ring's contents current; it
        # does not change what gets written to any clip.
        if mode != "off":
            self._push_ring(image)

    def _wants_recording(self, mode: str, motion_active: bool, now: float) -> bool:
        if mode == "continuous":
            return True
        if mode == "motion":
            if motion_active:
                self._last_active = now
                return True
            return self._writer is not None and now - self._last_active <= self._post_roll
        if mode == "event":
            if self._trigger_at is not None:
                if self._clip_trigger is None:
                    self._clip_trigger = self._trigger_label
                return True
            return False
        return False

    def _maybe_rotate_or_close(self, mode: str, now: float) -> None:
        if mode == "continuous" and now - self._clip_start >= self._segment:
            self._close_clip(now)
            return
        if mode == "motion" and now - self._last_active > self._post_roll:
            self._close_clip(now)
            return
        if mode == "event":
            with self._lock:
                trigger_at = self._trigger_at
            if trigger_at is not None and now - trigger_at > self._post_roll:
                self._close_clip(now)
                with self._lock:
                    self._trigger_at = None
                    self._trigger_label = None

    def _maybe_close_idle(self, now: float) -> None:
        if self._writer is None:
            return
        mode = self.current_mode()
        if mode != self._clip_mode:
            self._close_clip(now)
            return
        if mode == "motion" and now - self._last_active > self._post_roll:
            self._close_clip(now)
        elif mode == "event":
            with self._lock:
                trigger_at = self._trigger_at
            if trigger_at is not None and now - trigger_at > self._post_roll:
                self._close_clip(now)
                with self._lock:
                    self._trigger_at = None

    def _open_clip(self, image: np.ndarray, mode: str, now: float) -> None:
        height, width = image.shape[:2]
        self._size = (width, height)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(now))
        self._clip_seq += 1
        try:
            result = open_video_writer(
                self._dir / f"clip_{stamp}_{self._clip_seq:04d}", self._fps,
                self._size, self._codecs)
        except Exception:
            logger.exception("recorder disabling: video writer raised while opening")
            self.set_mode("off")
            self._status("codec_failed")
            return
        if result is None:
            logger.error("recorder disabling: no codec available")
            self.set_mode("off")
            self._status("codec_failed")
            return
        self._writer, self._writer_path = result
        self._clip_mode = mode
        self._clip_start = now
        self._status("recording")

    def _flush_ring(self) -> None:
        if self._writer is None:
            return
        for image in list(self._ring):
            self._write(image)

    def _write(self, image: np.ndarray) -> None:
        if self._writer is None or self._size is None:
            return
        if (image.shape[1], image.shape[0]) != self._size:
            image = cv2.resize(image, self._size)
        try:
            self._writer.write(image)
        except Exception:
            logger.exception("frame write failed")

    def _close_clip(self, now: float) -> None:
        if self._writer is None or self._writer_path is None:
            return
        self._writer.release()
        path = self._writer_path
        trigger = self._clip_trigger
        start = self._clip_start
        # Capture the mode the clip was actually recorded under BEFORE it is
        # cleared below. On the mode-mismatch close path in `_process`, this
        # fires precisely because the live mode just changed, so
        # `self.current_mode()` would already return the NEW mode (e.g.
        # "off") rather than the mode the clip was opened/recorded under
        # (e.g. "continuous") -- persisting that would corrupt the
        # recordings audit trail.
        clip_mode = self._clip_mode
        self._writer = None
        self._writer_path = None
        self._clip_mode = None
        self._clip_trigger = None
        size = path.stat().st_size if path.exists() else 0
        try:
            self._db.add_recording(
                kind="clip", path=str(path), start_ts=start, end_ts=now,
                mode=clip_mode if clip_mode is not None else self.current_mode(),
                trigger=trigger,
                source_id=self.source_id, size_bytes=size,
            )
        except Exception:
            logger.exception("failed to record clip metadata")
        self._status("idle")

    def _status(self, value: str) -> None:
        if self._on_status is not None:
            try:
                self._on_status(value)
            except Exception:
                logger.exception("recorder status callback failed")
