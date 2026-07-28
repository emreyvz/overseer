"""Play a local video file into the shared FrameBuffer as if it were a live camera.

Reads at the video's native frame rate (so it looks real-time, not fast-forwarded) and
loops seamlessly back to the start on end. Same interface as StreamReader/RtspReader, so
the backend can swap it in for a downloaded YouTube source. Because it plays a local file,
the feed never expires the way a signed HLS URL does.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import cv2

from camera.frame_buffer import Frame, FrameBuffer


class FileLoopReader(threading.Thread):
    def __init__(self, path: str, buffer: FrameBuffer,
                 on_status: Callable[[str], None] | None = None,
                 fps: float | None = None, reconnect_delay: float = 2.0) -> None:
        super().__init__(daemon=True, name="FileLoopReader")
        self.path = str(path)
        self._buffer = buffer
        self._on_status = on_status
        self._fps_override = fps
        self._delay = reconnect_delay
        self._stopped = threading.Event()
        self.frames_received = 0

    def _status(self, s: str) -> None:
        if self._on_status:
            try:
                self._on_status(s)
            except Exception:  # noqa: BLE001
                pass

    def _fps(self, cap: cv2.VideoCapture) -> float:
        if self._fps_override:
            return float(self._fps_override)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        return fps if 1.0 <= fps <= 120.0 else 25.0

    def run(self) -> None:
        seq = 0
        while not self._stopped.is_set():
            self._status("connecting")
            cap = cv2.VideoCapture(self.path, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap.release()
                self._status("reconnecting")
                self._stopped.wait(self._delay)
                continue
            frame_dt = 1.0 / self._fps(cap)
            self._status("connected")
            next_due = time.time()
            while not self._stopped.is_set():
                ok, img = cap.read()
                if not ok or img is None:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # loop back to the start
                    ok, img = cap.read()
                    if not ok or img is None:
                        break                              # not a decodable video -> reopen
                self._buffer.put(Frame(image=img, timestamp=time.time(), seq=seq))
                seq += 1
                self.frames_received += 1
                next_due += frame_dt                       # pace to the native frame rate
                sleep = next_due - time.time()
                if sleep > 0:
                    self._stopped.wait(sleep)
                else:
                    next_due = time.time()                 # fell behind; don't burst-catch-up
            cap.release()
            if not self._stopped.is_set():
                self._status("reconnecting")
                self._stopped.wait(self._delay)
        self._status("stopped")

    def stop(self) -> None:
        self._stopped.set()
