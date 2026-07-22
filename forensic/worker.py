"""Drop-oldest crop queue, background extraction worker, and analysis facade."""
from __future__ import annotations

import threading
import time
from collections import deque

from loguru import logger

from camera.frame_buffer import Frame
from core.config import Config
from forensic.attributes import ClassicalAttributes
from forensic.index import MetadataIndex
from forensic.tracklet import CropJob, TrackletManager, TrackletView
from plugins.base import Detection
from storage.snapshots import SnapshotService


class CropQueue:
    def __init__(self, maxsize: int) -> None:
        self._maxsize = max(1, maxsize)
        self._queue: deque[CropJob] = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self.dropped = 0

    def put(self, job: CropJob) -> None:
        with self._not_empty:
            if len(self._queue) >= self._maxsize:
                self._queue.popleft()
                self.dropped += 1
            self._queue.append(job)
            self._not_empty.notify()

    def drain(self, n: int) -> list[CropJob]:
        with self._lock:
            out = []
            while self._queue and len(out) < n:
                out.append(self._queue.popleft())
            return out

    def wait(self, timeout: float) -> None:
        with self._not_empty:
            if not self._queue:
                self._not_empty.wait(timeout)

    def __len__(self) -> int:
        with self._lock:
            return len(self._queue)


class ForensicWorker(threading.Thread):
    def __init__(
        self,
        queue: CropQueue,
        index: MetadataIndex,
        snapshots: SnapshotService,
        embedder: object | None = None,
        attribute_model: object | None = None,
        batch_size: int = 8,
    ) -> None:
        super().__init__(daemon=True, name="ForensicWorker")
        self._queue = queue
        self._index = index
        self._snapshots = snapshots
        self._embedder = embedder
        self._attribute_model = attribute_model
        self._batch_size = max(1, batch_size)
        # Named _stop_event (not _stop) to avoid shadowing threading.Thread's
        # private _stop bound method, which Thread.join() invokes internally
        # once the thread finishes; overwriting it with an Event breaks join().
        self._stop_event = threading.Event()

    def process_batch(self, jobs: list[CropJob]) -> None:
        if not jobs:
            return
        crops = [job.crop for job in jobs]
        clothing: list[str | None] = [None] * len(jobs)
        if self._attribute_model is not None:
            try:
                clothing = list(self._attribute_model.classify(crops))
            except Exception:
                logger.exception("attribute model classify failed")
        vectors = None
        if self._embedder is not None:
            try:
                vectors = self._embedder.embed(crops)
            except Exception:
                logger.exception("reid embed failed")
        now = time.time()
        for i, job in enumerate(jobs):
            if i < len(clothing) and clothing[i] is not None:
                job.attributes.clothing_type = clothing[i]
            try:
                path = self._snapshots.save(job.crop, prefix="tracklet")
                self._index.save_sample(job.tracklet_id, job.attributes, str(path), now)
            except OSError:
                logger.exception("tracklet snapshot failed")
                self._index.save_sample(job.tracklet_id, job.attributes, None, now)
            if vectors is not None and i < len(vectors) and vectors[i].size:
                self._index.set_embedding(job.tracklet_id, vectors[i], now)

    def run(self) -> None:
        while not self._stop_event.is_set():
            self._queue.wait(0.5)
            try:
                self.process_batch(self._queue.drain(self._batch_size))
            except Exception:
                logger.exception("forensic batch failed")
        self.process_batch(self._queue.drain(10_000))  # drain on stop

    def stop(self) -> None:
        self._stop_event.set()


class ForensicFacade:
    def __init__(
        self,
        config: Config,
        index: MetadataIndex,
        snapshots: SnapshotService,
        embedder: object | None = None,
        attribute_model: object | None = None,
    ) -> None:
        self.enabled = bool(config.get("forensic.enabled", True))
        self._manager = TrackletManager(
            config, index.ensure_tracklet, ClassicalAttributes()
        )
        self._queue = CropQueue(int(config.get("forensic.queue_size", 64)))
        self._worker = ForensicWorker(
            self._queue,
            index,
            snapshots,
            embedder,
            attribute_model,
            batch_size=int(config.get("forensic.batch_size", 8)),
        )

    def start(self) -> None:
        self._worker.start()

    def stop(self) -> None:
        self._worker.stop()
        if self._worker.is_alive():
            self._worker.join(timeout=5)

    def reset(self) -> None:
        self._manager.reset()

    def offer(
        self,
        source_id: int | None,
        frame: Frame,
        persons: list[Detection],
        accessories: list[Detection],
    ) -> list[TrackletView]:
        if not self.enabled:
            return []
        jobs, views = self._manager.update(source_id, frame, persons, accessories,
                                           time.time())
        for job in jobs:
            self._queue.put(job)
        return views
