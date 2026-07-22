"""Lightweight per-camera thumbnail relay for the source picker (item 4).

Opens each requested MJPEG source in parallel, keeps only the latest raw JPEG
(no cv2 decode, no analysis), and reaps idle workers after a TTL. This lets the
picker show live movement for every camera without the full pipeline.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import cv2
import requests

from camera.mjpeg_parser import MJPEGParser

from .ytstream import is_stream_url, resolve_stream

log = logging.getLogger("overseer.thumbs")


class ThumbWorker(threading.Thread):
    def __init__(self, url: str) -> None:
        super().__init__(daemon=True, name="ThumbWorker")
        self.url = url
        self.latest: bytes | None = None
        self.last_access = time.time()
        self._stop = threading.Event()

    def run(self) -> None:
        parser = MJPEGParser()
        while not self._stop.is_set():
            try:
                with requests.get(self.url, stream=True, timeout=(6, 6)) as r:
                    for chunk in r.iter_content(16384):
                        if self._stop.is_set():
                            break
                        for jpeg in parser.feed(chunk):
                            self.latest = jpeg
            except Exception:  # noqa: BLE001 - unreachable source -> no frame (picker shows static)
                pass
            self._stop.wait(2.0)

    def stop(self) -> None:
        self._stop.set()


class Cv2ThumbWorker(threading.Thread):
    """Thumbnail relay for stream URLs (YouTube live / RTSP): resolve → cv2 decode
    → downscaled JPEG at a low rate, so the map can preview these feeds too."""

    def __init__(self, url: str) -> None:
        super().__init__(daemon=True, name="Cv2ThumbWorker")
        self.url = url
        self.latest: bytes | None = None
        self.last_access = time.time()
        self._stopped = threading.Event()

    def run(self) -> None:
        while not self._stopped.is_set():
            media = resolve_stream(self.url) if is_stream_url(self.url) else self.url
            if not media:
                self._stopped.wait(3.0)
                continue
            cap = cv2.VideoCapture(media, cv2.CAP_FFMPEG)
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:  # noqa: BLE001
                pass
            if not cap.isOpened():
                cap.release()
                self._stopped.wait(3.0)
                continue
            last_enc = 0.0
            while not self._stopped.is_set():
                ok, img = cap.read()
                if not ok or img is None:
                    break
                now = time.time()
                if now - last_enc >= 0.35:  # ~3 fps preview
                    last_enc = now
                    h, w = img.shape[:2]
                    if w > 480:
                        img = cv2.resize(img, (480, max(1, int(h * 480 / w))))
                    ok2, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 68])
                    if ok2:
                        self.latest = buf.tobytes()
            cap.release()
            if not self._stopped.is_set():
                self._stopped.wait(2.0)

    def stop(self) -> None:
        self._stopped.set()


class ThumbHub:
    """Warm-connection pool for previews: keeps recently-used cameras decoding so
    they are ready instantly on any screen. Bounded by TTL and a max worker count
    (least-recently-used evicted) to cap memory/CPU."""

    def __init__(self, ttl: float = 120.0, max_workers: int = 16, cache_dir: Path | None = None) -> None:
        self.ttl = ttl
        self.max_workers = max_workers
        self._workers: dict[int, ThumbWorker | Cv2ThumbWorker] = {}
        # Last frame per camera, kept even after its worker is stopped AND persisted
        # to disk, so a camera that was seen once always keeps a thumbnail — across
        # evictions and even across app restarts — instead of dropping to NO SIGNAL.
        self._last: dict[int, bytes] = {}
        self._saved: dict[int, float] = {}
        self._dir = cache_dir
        self._lock = threading.RLock()
        if cache_dir is not None:
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                for p in cache_dir.glob("*.jpg"):
                    try:
                        self._last[int(p.stem)] = p.read_bytes()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception:  # noqa: BLE001
                pass

    def _remember(self, sid: int, jpeg: bytes) -> None:
        self._last[sid] = jpeg
        if self._dir is not None and time.time() - self._saved.get(sid, 0.0) > 8.0:
            self._saved[sid] = time.time()
            try:
                (self._dir / f"{sid}.jpg").write_bytes(jpeg)
            except Exception:  # noqa: BLE001
                pass

    def get_jpeg(self, source_id: int, url: str) -> bytes | None:
        with self._lock:
            w = self._workers.get(source_id)
            if w is None:
                w = Cv2ThumbWorker(url) if is_stream_url(url) else ThumbWorker(url)
                self._workers[source_id] = w
                w.start()
                self._evict_lru()
            w.last_access = time.time()
            if w.latest:
                self._remember(source_id, w.latest)
            return w.latest or self._last.get(source_id)  # frozen last frame while (re)warming

    def _evict_lru(self) -> None:
        while len(self._workers) > self.max_workers:
            sid = min(self._workers, key=lambda k: self._workers[k].last_access)
            w = self._workers.pop(sid)
            if w.latest:
                self._remember(sid, w.latest)  # preserve its last thumbnail
            w.stop()

    def reap(self) -> None:
        now = time.time()
        with self._lock:
            for sid, w in list(self._workers.items()):
                if now - w.last_access > self.ttl:
                    if w.latest:
                        self._remember(sid, w.latest)
                    w.stop()
                    del self._workers[sid]

    def stop_all(self) -> None:
        with self._lock:
            for w in self._workers.values():
                w.stop()
            self._workers.clear()
