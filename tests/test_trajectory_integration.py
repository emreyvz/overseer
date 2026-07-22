# tests/test_trajectory_integration.py
from pathlib import Path

from core.config import load_config
from plugins.base import Detection
from trajectory.monitor import TrajectoryMonitor


def _config(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("trajectory:\n  running_speed: 100.0\n  stopped_seconds: 2.0\n"
                 "  stopped_eps: 8.0\n  uturn_window: 6\n  uturn_angle: 150.0\n"
                 "  buffer_size: 30\n  expire_seconds: 5.0\n", encoding="utf-8")
    return load_config(p)


def _p(tid: int, cx: int, cy: int) -> Detection:
    return Detection("person", 0.9, (cx - 5, cy - 20, cx + 5, cy), "person", track_id=tid)


def test_running_then_stopped_flow(tmp_path: Path) -> None:
    mon = TrajectoryMonitor(_config(tmp_path))
    events = []
    events += mon.process([_p(7, 0, 100)], 0.0)
    events += mon.process([_p(7, 300, 100)], 1.0)          # fast -> running
    for i in range(2, 7):
        events += mon.process([_p(7, 300, 100)], float(i))  # stationary -> stopped fires during
    names = [e.event_type.name for e in events]
    assert "RUNNING" in names
    assert "STOPPED" in names
