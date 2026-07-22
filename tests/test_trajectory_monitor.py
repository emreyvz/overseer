from pathlib import Path

from core.config import load_config
from plugins.base import Detection
from trajectory.monitor import TrajectoryEvent, TrajectoryMonitor


def _config(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "trajectory:\n  buffer_size: 30\n  expire_seconds: 5.0\n"
        "  running_speed: 100.0\n  stopped_seconds: 3.0\n  stopped_eps: 10.0\n"
        "  uturn_window: 6\n  uturn_angle: 150.0\n",
        encoding="utf-8")
    return load_config(p)


def _person(track_id: int, cx: int, cy: int) -> Detection:
    return Detection("person", 0.9, (cx - 5, cy - 20, cx + 5, cy), "person",
                     track_id=track_id)


def test_running(tmp_path: Path) -> None:
    mon = TrajectoryMonitor(_config(tmp_path))
    mon.process([_person(7, 0, 100)], now=0.0)
    ev = mon.process([_person(7, 200, 100)], now=1.0)  # 200 px / 1 s = 200 >= 100
    assert [e.event_type.name for e in ev] == ["RUNNING"]
    # once, until speed drops
    assert mon.process([_person(7, 400, 100)], now=2.0) == []


def test_not_running_slow(tmp_path: Path) -> None:
    mon = TrajectoryMonitor(_config(tmp_path))
    mon.process([_person(7, 0, 100)], now=0.0)
    assert mon.process([_person(7, 20, 100)], now=1.0) == []  # 20 px/s < 100


def test_stopped(tmp_path: Path) -> None:
    mon = TrajectoryMonitor(_config(tmp_path))
    events = []
    for i in range(6):
        events += mon.process([_person(7, 100, 100)], now=float(i))  # stationary
    assert any(e.event_type.name == "STOPPED" for e in events)


def test_stopped_with_jittery_timestamps(tmp_path: Path) -> None:
    # Real wall-clock frame timing never lands on the stopped_seconds boundary;
    # STOPPED must still fire (regression for the window/buffer never-fires bug).
    mon = TrajectoryMonitor(_config(tmp_path))     # stopped_seconds=3.0
    events = []
    t = 0.0
    for i in range(50):                            # ~5s of ~0.1s jittery frames
        t += 0.1 + (0.017 if i % 3 else -0.011)    # never grid-aligned
        events += mon.process([_person(7, 100, 100)], now=t)   # stationary
    assert any(e.event_type.name == "STOPPED" for e in events)


def test_stopped_rearms_after_moving(tmp_path: Path) -> None:
    mon = TrajectoryMonitor(_config(tmp_path))
    events = []
    for i in range(5):                             # stationary -> STOPPED
        events += mon.process([_person(7, 100, 100)], now=float(i))
    assert any(e.event_type.name == "STOPPED" for e in events)
    # move well beyond stopped_eps, then hold still again -> STOPPED re-arms
    events = []
    for i in range(5, 12):
        cx = 100 + (i - 4) * 40 if i < 8 else 260
        events += mon.process([_person(7, cx, 100)], now=float(i))
    assert any(e.event_type.name == "STOPPED" for e in events)


def test_uturn(tmp_path: Path) -> None:
    mon = TrajectoryMonitor(_config(tmp_path))
    # go right then reverse left
    xs = [0, 20, 40, 60, 40, 20, 0]
    events = []
    for i, x in enumerate(xs):
        events += mon.process([_person(7, x, 100)], now=float(i))
    assert any(e.event_type.name == "U_TURN" for e in events)


def test_reset_and_expire(tmp_path: Path) -> None:
    mon = TrajectoryMonitor(_config(tmp_path))
    mon.process([_person(7, 0, 100)], now=0.0)
    mon.reset()
    # after reset, a fresh fast move re-detects running (no stale suppression)
    mon.process([_person(7, 0, 100)], now=10.0)
    ev = mon.process([_person(7, 300, 100)], now=11.0)
    assert [e.event_type.name for e in ev] == ["RUNNING"]
    assert isinstance(ev[0], TrajectoryEvent)
