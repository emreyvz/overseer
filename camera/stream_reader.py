"""Background thread reading an MJPEG stream with automatic reconnect."""
from __future__ import annotations

import threading
import time
from typing import Callable

import cv2
import numpy as np
import requests
from loguru import logger

from camera.frame_buffer import Frame, FrameBuffer
from camera.mjpeg_parser import MJPEGParser

StatusCallback = Callable[[str], None]


class StreamReader(threading.Thread):
    def __init__(
        self,
        url: str,
        buffer: FrameBuffer,
        on_status: StatusCallback | None = None,
        connect_timeout: float = 10.0,
        read_timeout: float = 10.0,
        reconnect_min_delay: float = 1.0,
        reconnect_max_delay: float = 60.0,
    ) -> None:
        super().__init__(daemon=True, name=f"StreamReader({url[:40]})")
        self._url = url
        self._buffer = buffer
        self._on_status = on_status
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._min_delay = reconnect_min_delay
        self._max_delay = reconnect_max_delay
        self._stop_event = threading.Event()
        self.frames_received = 0
        self.decode_failures = 0

    def _status(self, value: str) -> None:
        if self._on_status is not None:
            try:
                self._on_status(value)
            except Exception:
                logger.exception("status callback failed")

    def run(self) -> None:
        from core.thread_priority import set_current_thread_priority, ABOVE_NORMAL
        set_current_thread_priority(ABOVE_NORMAL)   # display source: keep frames arriving at camera rate
        delay = self._min_delay
        first_attempt = True
        while not self._stop_event.is_set():
            self._status("connecting" if first_attempt else "reconnecting")
            frames_before = self.frames_received
            try:
                self._read_stream()
            except requests.RequestException as exc:
                logger.warning("stream error: {}", exc)
            if self._stop_event.is_set():
                break
            got_frames = self.frames_received > frames_before
            delay = self._min_delay if got_frames else min(delay * 2, self._max_delay)
            first_attempt = False
            self._stop_event.wait(delay)
        self._status("stopped")

    def _read_stream(self) -> None:
        parser = MJPEGParser()
        with requests.get(
            self._url, stream=True,
            timeout=(self._connect_timeout, self._read_timeout),
        ) as response:
            response.raise_for_status()
            self._status("connected")
            for chunk in response.iter_content(chunk_size=16384):
                if self._stop_event.is_set():
                    return
                for jpeg in parser.feed(chunk):
                    image = cv2.imdecode(
                        np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR
                    )
                    if image is None:
                        self.decode_failures += 1
                        continue
                    self._buffer.put(Frame(
                        image=image, timestamp=time.time(), seq=self.frames_received
                    ))
                    self.frames_received += 1

    def stop(self) -> None:
        self._stop_event.set()


def probe_mjpeg(url: str, timeout: float = 8.0) -> tuple[bool, str]:
    """Used by the source management 'Test Connection' button: is the URL a real MJPEG stream?"""
    try:
        with requests.get(url, stream=True, timeout=(timeout, timeout)) as response:
            if response.status_code != 200:
                return False, f"HTTP {response.status_code}"
            content_type = response.headers.get("Content-Type", "")
            if "multipart/x-mixed-replace" not in content_type:
                return False, f"Not MJPEG: {content_type or 'unknown content'}"
            parser = MJPEGParser()
            deadline = time.time() + timeout
            for chunk in response.iter_content(chunk_size=16384):
                if parser.feed(chunk):
                    return True, "Connection ok, frame received"
                if time.time() > deadline:
                    break
            return False, "Connected but no frame received"
    except requests.RequestException as exc:
        return False, f"Connection error: {exc.__class__.__name__}"
