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
        from core.thread_priority import set_current_thread_priority, ABOVE_NORMAL
        set_current_thread_priority(ABOVE_NORMAL)   # display source: keep frames arriving at camera rate
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
            # Pace to the frame's real presentation timestamp (CAP_PROP_POS_MSEC) so the
            # video plays at true real time no matter what the container's reported fps says
            # — a wrong/variable fps can otherwise make it run several times too fast. Fall
            # back to fixed frame_dt when PTS is unavailable, or when an explicit fps is set
            # (tests use that to run fast).
            use_pts = self._fps_override is None
            self._status("connected")
            anchor_wall: float | None = None
            anchor_pts = 0.0
            next_due = time.time()
            while not self._stopped.is_set():
                ok, img = cap.read()
                if not ok or img is None:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)   # loop back to the start
                    anchor_wall = None                     # re-anchor timing after the wrap
                    next_due = time.time()
                    ok, img = cap.read()
                    if not ok or img is None:
                        break                              # not a decodable video -> reopen
                now = time.time()
                pts = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0 if use_pts else 0.0
                if use_pts and pts > 0.0:
                    if anchor_wall is None or pts < anchor_pts:
                        anchor_wall, anchor_pts = now, pts
                    target = anchor_wall + (pts - anchor_pts)
                else:
                    target = next_due
                sleep = target - now
                if sleep > 0:
                    self._stopped.wait(min(sleep, 1.0))    # cap so stop() stays responsive
                next_due = max(target, time.time()) + frame_dt
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
