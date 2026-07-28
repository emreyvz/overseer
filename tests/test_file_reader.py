import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from camera.file_reader import FileLoopReader


class _Buf:
    def __init__(self) -> None:
        self.frames = []
        self._lock = threading.Lock()

    def put(self, frame) -> None:
        with self._lock:
            self.frames.append(frame)


def _make_video(path: Path, n: int = 5, fps: int = 10) -> bool:
    w, h = 32, 24
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not vw.isOpened():
        return False
    for i in range(n):
        vw.write(np.full((h, w, 3), (i + 1) * 40 % 256, np.uint8))
    vw.release()
    return path.exists() and path.stat().st_size > 0


def test_loops_past_end(tmp_path: Path) -> None:
    vid = tmp_path / "clip.mp4"
    if not _make_video(vid, n=5, fps=10):
        pytest.skip("no mp4 writer available in this OpenCV build")
    buf = _Buf()
    statuses: list[str] = []
    r = FileLoopReader(str(vid), buf, on_status=statuses.append, fps=120)
    r.start()
    time.sleep(0.4)                 # at 120 fps, 0.4s >> 5 frames -> must have looped
    r.stop()
    r.join(timeout=2)
    assert r.frames_received > 5    # wrapped back to the start at least once
    assert "connected" in statuses
    assert statuses[-1] == "stopped"


def test_feeds_buffer(tmp_path: Path) -> None:
    vid = tmp_path / "clip.mp4"
    if not _make_video(vid, n=4, fps=10):
        pytest.skip("no mp4 writer available")
    buf = _Buf()
    r = FileLoopReader(str(vid), buf, fps=120)
    r.start()
    time.sleep(0.25)
    r.stop(); r.join(timeout=2)
    assert len(buf.frames) > 0
    assert buf.frames[0].image is not None


def test_missing_file_reconnects_then_stops(tmp_path: Path) -> None:
    buf = _Buf()
    statuses: list[str] = []
    r = FileLoopReader(str(tmp_path / "nope.mp4"), buf, on_status=statuses.append,
                       reconnect_delay=0.05)
    r.start()
    time.sleep(0.2)
    r.stop(); r.join(timeout=2)
    assert r.frames_received == 0
    assert "reconnecting" in statuses     # couldn't open -> retried
    assert statuses[-1] == "stopped"
