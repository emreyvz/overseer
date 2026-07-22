import time
from typing import Callable

import requests

from camera.frame_buffer import FrameBuffer
from camera.stream_reader import StreamReader


def wait_until(cond: Callable[[], bool], timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_receives_frames(mjpeg_server) -> None:
    url = mjpeg_server()
    buf = FrameBuffer(maxsize=10)
    reader = StreamReader(url, buf, reconnect_min_delay=0.1, reconnect_max_delay=0.5)
    reader.start()
    try:
        assert wait_until(lambda: reader.frames_received >= 5)
        frame = buf.get(timeout=2.0)
        assert frame is not None
        assert frame.image.shape == (120, 160, 3)
        assert frame.seq >= 0
    finally:
        reader.stop()
        reader.join(timeout=5)
    assert not reader.is_alive()


def test_reconnects_after_disconnect(mjpeg_server) -> None:
    url = mjpeg_server(frames_per_connection=3)  # server closes every 3 frames
    buf = FrameBuffer(maxsize=10)
    statuses: list[str] = []
    reader = StreamReader(url, buf, on_status=statuses.append,
                          reconnect_min_delay=0.05, reconnect_max_delay=0.2)
    reader.start()
    try:
        assert wait_until(lambda: reader.frames_received >= 8)  # >= 3 connections
    finally:
        reader.stop()
        reader.join(timeout=5)
    assert "reconnecting" in statuses
    assert statuses.count("connected") >= 2


def test_stop_while_unreachable() -> None:
    buf = FrameBuffer(maxsize=5)
    reader = StreamReader("http://127.0.0.1:1/none.mjpg", buf,
                          connect_timeout=0.2, reconnect_min_delay=0.05,
                          reconnect_max_delay=0.2)
    reader.start()
    time.sleep(0.5)
    reader.stop()
    reader.join(timeout=5)
    assert not reader.is_alive()


def test_probe_mjpeg_ok(mjpeg_server) -> None:
    from camera.stream_reader import probe_mjpeg

    ok, message = probe_mjpeg(mjpeg_server(), timeout=5.0)
    assert ok is True
    assert "frame" in message


def test_probe_mjpeg_unreachable() -> None:
    from camera.stream_reader import probe_mjpeg

    ok, message = probe_mjpeg("http://127.0.0.1:1/none.mjpg", timeout=0.5)
    assert ok is False
    assert "error" in message.lower()


class _OneFrameThenDropStreamReader(StreamReader):
    """Test double: every connection cycle receives exactly one frame, then the
    connection dies with a RequestException (simulates a repeatedly-dropping
    but otherwise working camera)."""

    def _read_stream(self) -> None:
        self.frames_received += 1
        raise requests.RequestException("simulated mid-stream drop")


def test_backoff_resets_after_mid_stream_failure() -> None:
    buf = FrameBuffer(maxsize=5)
    statuses: list[str] = []
    reader = _OneFrameThenDropStreamReader(
        "http://127.0.0.1:1/none.mjpg", buf, on_status=statuses.append,
        reconnect_min_delay=0.05, reconnect_max_delay=10.0,
    )
    reader.start()
    try:
        time.sleep(1.5)
    finally:
        reader.stop()
        reader.join(timeout=5)
    assert not reader.is_alive()

    # If backoff wrongly escalated toward the max delay, only ~2-3 cycles
    # would fit in 1.5s (0.1 + 0.2 + 0.4 + 0.8 = 1.5). With the fix, delay
    # stays pinned near reconnect_min_delay (0.05s), so many more fit.
    reconnecting_count = statuses.count("reconnecting")
    assert reconnecting_count >= 4
