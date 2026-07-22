import numpy as np

from camera.frame_buffer import Frame
from camera.health import HealthMonitor


def make_frame(seq: int, ts: float, fill: int) -> Frame:
    img = np.full((64, 64, 3), fill, dtype=np.uint8)
    return Frame(image=img, timestamp=ts, seq=seq)


def test_healthy_stream() -> None:
    mon = HealthMonitor(freeze_timeout=10.0)
    for i in range(5):
        mon.observe(make_frame(i, float(i), fill=i * 40))
    assert mon.check(now=5.0) == []


def test_no_frames_detected() -> None:
    mon = HealthMonitor(freeze_timeout=10.0)
    mon.observe(make_frame(0, 0.0, fill=100))
    assert mon.check(now=5.0) == []
    assert "no_frames" in mon.check(now=11.0)


def test_frozen_stream_detected() -> None:
    mon = HealthMonitor(freeze_timeout=10.0)
    for i in range(20):
        mon.observe(make_frame(i, float(i), fill=100))  # always same image
    issues = mon.check(now=19.0)
    assert "frozen" in issues
    assert "no_frames" not in issues


def test_recovers_after_change() -> None:
    mon = HealthMonitor(freeze_timeout=10.0)
    for i in range(20):
        mon.observe(make_frame(i, float(i), fill=100))
    assert "frozen" in mon.check(now=19.0)
    mon.observe(make_frame(20, 20.0, fill=200))  # image changed
    assert mon.check(now=20.5) == []


def test_no_frames_before_first_frame() -> None:
    mon = HealthMonitor(freeze_timeout=10.0)
    mon.reset(now=0.0)
    assert mon.check(now=11.0) == ["no_frames"]
