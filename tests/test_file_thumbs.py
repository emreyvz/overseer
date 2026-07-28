import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from server.thumbs import FileThumbWorker, ThumbHub, _make_worker


def _make_video(path: Path, n: int = 5, fps: int = 10) -> bool:
    vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (48, 32))
    if not vw.isOpened():
        return False
    for i in range(n):
        vw.write(np.full((32, 48, 3), (i + 1) * 40 % 256, np.uint8))
    vw.release()
    return path.exists() and path.stat().st_size > 0


def _wait(fn, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def test_make_worker_picks_file_for_local(tmp_path: Path) -> None:
    vid = tmp_path / "clip.mp4"
    if not _make_video(vid):
        pytest.skip("no mp4 writer")
    w = _make_worker(str(vid))
    assert isinstance(w, FileThumbWorker)


def test_make_worker_picks_stream_for_youtube() -> None:
    from server.thumbs import Cv2ThumbWorker
    assert isinstance(_make_worker("https://youtu.be/dQw4w9WgXcQ"), Cv2ThumbWorker)


def test_file_thumb_worker_produces_jpeg(tmp_path: Path) -> None:
    vid = tmp_path / "clip.mp4"
    if not _make_video(vid, n=6, fps=10):
        pytest.skip("no mp4 writer")
    w = FileThumbWorker(str(vid))
    w.start()
    try:
        jpeg = _wait(lambda: w.latest)
        assert jpeg is not None and jpeg[:2] == b"\xff\xd8"   # JPEG SOI
    finally:
        w.stop(); w.join(timeout=2)


def test_thumbhub_serves_local_file(tmp_path: Path) -> None:
    vid = tmp_path / "clip.mp4"
    if not _make_video(vid, n=6, fps=10):
        pytest.skip("no mp4 writer")
    hub = ThumbHub(cache_dir=tmp_path / "cache")
    try:
        jpeg = _wait(lambda: hub.get_jpeg(1, str(vid)))
        assert jpeg is not None and jpeg[:2] == b"\xff\xd8"
    finally:
        hub.stop_all()


def test_thumbhub_recreates_worker_on_target_change(tmp_path: Path) -> None:
    vid = tmp_path / "clip.mp4"
    if not _make_video(vid, n=6, fps=10):
        pytest.skip("no mp4 writer")
    hub = ThumbHub(cache_dir=tmp_path / "cache")
    try:
        # first as a (would-be) stream URL, then switch to the downloaded local file
        hub.get_jpeg(1, "https://youtu.be/dQw4w9WgXcQ")
        w1 = hub._workers[1]
        hub.get_jpeg(1, str(vid))
        w2 = hub._workers[1]
        assert w2 is not w1
        assert isinstance(w2, FileThumbWorker)
    finally:
        hub.stop_all()
