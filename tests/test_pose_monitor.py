# tests/test_pose_monitor.py
from pathlib import Path

from core.config import load_config
from plugins.base import Detection
from pose.monitor import PoseEvent, PoseMonitor


def _config(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "pose:\n  buffer_size: 30\n  expire_seconds: 5.0\n  standing_aspect: 1.2\n"
        "  fallen_aspect: 0.8\n  crowd_min: 3\n  fight_distance: 60.0\n"
        "  fight_motion: 50.0\n",
        encoding="utf-8")
    return load_config(p)


def _person(track_id: int, x1: int, y1: int, x2: int, y2: int) -> Detection:
    return Detection("person", 0.9, (x1, y1, x2, y2), "person", track_id=track_id)


def _standing(tid: int, cx: int, cy: int) -> Detection:
    return _person(tid, cx - 15, cy - 60, cx + 15, cy)   # h=60 w=30 -> aspect 2.0


def _fallen(tid: int, cx: int, cy: int) -> Detection:
    return _person(tid, cx - 40, cy - 15, cx + 40, cy)   # h=15 w=80 -> aspect ~0.19


def test_falling(tmp_path: Path) -> None:
    mon = PoseMonitor(_config(tmp_path))
    assert mon.process([_standing(7, 100, 100)], 0.0) == []       # upright
    ev = mon.process([_fallen(7, 100, 100)], 1.0)                 # collapsed
    assert [e.event_type.name for e in ev] == ["FALLING"]
    assert isinstance(ev[0], PoseEvent)
    assert mon.process([_fallen(7, 100, 100)], 2.0) == []         # latched


def test_crowding(tmp_path: Path) -> None:
    mon = PoseMonitor(_config(tmp_path))
    two = [_standing(1, 10, 100), _standing(2, 50, 100)]
    assert mon.process(two, 0.0) == []                            # 2 < crowd_min 3
    three = two + [_standing(3, 90, 100)]
    ev = mon.process(three, 1.0)
    assert any(e.event_type.name == "CROWDING" and e.metadata["count"] == 3
               for e in ev)
    assert not any(e.event_type.name == "CROWDING"               # latched
                   for e in mon.process(three, 2.0))


def test_fighting(tmp_path: Path) -> None:
    mon = PoseMonitor(_config(tmp_path))
    # two close persons BOTH moving fast between frames (fight needs both active)
    mon.process([_standing(1, 100, 100), _standing(2, 150, 100)], 0.0)
    ev = mon.process([_standing(1, 150, 100), _standing(2, 100, 100)], 1.0)
    # both moved 50px/1s >= fight_motion 50; centers 50px apart <= fight_distance 60
    assert any(e.event_type.name == "FIGHTING" for e in ev)


def test_no_fight_when_still(tmp_path: Path) -> None:
    mon = PoseMonitor(_config(tmp_path))
    mon.process([_standing(1, 100, 100), _standing(2, 130, 100)], 0.0)
    ev = mon.process([_standing(1, 101, 100), _standing(2, 130, 100)], 1.0)
    assert not any(e.event_type.name == "FIGHTING" for e in ev)  # slow -> no fight


def test_reset(tmp_path: Path) -> None:
    mon = PoseMonitor(_config(tmp_path))
    mon.process([_standing(7, 100, 100)], 0.0)
    mon.reset()
    assert mon.process([_fallen(7, 100, 100)], 1.0) == []         # no upright history
    ev = mon.process([_standing(7, 100, 100)], 2.0)
    assert ev == [] and isinstance(mon.process([], 3.0), list)
