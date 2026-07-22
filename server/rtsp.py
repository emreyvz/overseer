"""RTSP/RTMP reader (feature 25): cv2.VideoCapture into the shared FrameBuffer,
same interface as camera.StreamReader so the backend can swap by URL scheme."""
from __future__ import annotations

import os
import threading
import time
from typing import Callable

import cv2

from camera.frame_buffer import Frame, FrameBuffer

# Low-latency FFMPEG demux: don't buffer segments, so reads stay near real time
# (prevents the freeze-then-fast-forward on high-fps HLS/YouTube feeds).
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "fflags;nobuffer|flags;low_delay")


class RtspReader(threading.Thread):
    def __init__(self, url: str, buffer: FrameBuffer, on_status: Callable[[str], None] | None = None,
                 reconnect_delay: float = 3.0) -> None:
        super().__init__(daemon=True, name="RtspReader")
        self.url = url
        self._buffer = buffer
        self._on_status = on_status
        self._delay = reconnect_delay
        self._stopped = threading.Event()
        self.frames_received = 0

    def _status(self, s: str) -> None:
        if self._on_status:
            try:
                self._on_status(s)
            except Exception:  # noqa: BLE001
                pass

    def run(self) -> None:
        seq = 0
        while not self._stopped.is_set():
            self._status("connecting")
            cap = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap.release()
                self._status("reconnecting")
                self._stopped.wait(self._delay)
                continue
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # keep only the newest frame → real-time
            except Exception:  # noqa: BLE001
                pass
            self._status("connected")
            while not self._stopped.is_set():
                ok, img = cap.read()
                if not ok or img is None:
                    break
                self._buffer.put(Frame(image=img, timestamp=time.time(), seq=seq))
                seq += 1
                self.frames_received += 1
            cap.release()
            if not self._stopped.is_set():
                self._status("reconnecting")
                self._stopped.wait(self._delay)
        self._status("stopped")

    def stop(self) -> None:
        self._stopped.set()
