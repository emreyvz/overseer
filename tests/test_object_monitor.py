from pathlib import Path

from core.config import load_config
from plugins.base import Detection
from objects.monitor import ObjectEvent, ObjectMonitor


def _config(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "objects:\n  buffer_size: 300\n  expire_seconds: 30.0\n  abandon_seconds: 3.0\n"
        "  obj_eps: 20.0\n  owner_distance: 100.0\n  stable_seconds: 2.0\n"
        "  removed_seconds: 3.0\n",
        encoding="utf-8")
    return load_config(p)


def _bag(tid: int, cx: int, cy: int) -> Detection:
    return Detection("backpack", 0.9, (cx - 10, cy - 10, cx + 10, cy + 10),
                     "accessory", track_id=tid)


def _person(cx: int, cy: int) -> Detection:
    return Detection("person", 0.9, (cx - 15, cy - 60, cx + 15, cy), "person", track_id=99)


def test_abandoned(tmp_path: Path) -> None:
    mon = ObjectMonitor(_config(tmp_path))
    events = []
    for i in range(5):
        events += mon.process([_bag(5, 200, 200)], [], now=float(i))  # stationary, no owner
    abandoned = [e for e in events if e.event_type.name == "ABANDONED_OBJECT"]
    assert abandoned
    assert isinstance(abandoned[0], ObjectEvent)


def test_not_abandoned_with_owner(tmp_path: Path) -> None:
    mon = ObjectMonitor(_config(tmp_path))
    events = []
    for i in range(5):
        # a person standing right next to the bag the whole time
        events += mon.process([_bag(5, 200, 200)], [_person(210, 250)], now=float(i))
    assert not any(e.event_type.name == "ABANDONED_OBJECT" for e in events)


def test_removed(tmp_path: Path) -> None:
    mon = ObjectMonitor(_config(tmp_path))
    for i in range(3):
        mon.process([_bag(5, 200, 200)], [_person(210, 250)], now=float(i))  # established
    # bag disappears; owner still there so no abandon confusion
    events = []
    for i in range(4, 8):
        events += mon.process([], [_person(210, 250)], now=float(i))
    assert any(e.event_type.name == "REMOVED_OBJECT" for e in events)


def test_no_remove_if_brief(tmp_path: Path) -> None:
    mon = ObjectMonitor(_config(tmp_path))
    mon.process([_bag(5, 200, 200)], [], now=0.0)   # seen once (< stable_seconds)
    events = []
    for i in range(1, 6):
        events += mon.process([], [], now=float(i))
    assert not any(e.event_type.name == "REMOVED_OBJECT" for e in events)


def test_abandoned_with_jittery_timestamps(tmp_path: Path) -> None:
    mon = ObjectMonitor(_config(tmp_path))   # abandon_seconds=3.0
    events = []
    t = 0.0
    for i in range(60):                       # ~6s of ~0.1s jittery frames
        t += 0.1 + (0.017 if i % 3 else -0.011)   # deterministic jitter, never grid-aligned
        events += mon.process([_bag(5, 200, 200)], [], now=t)   # stationary, no owner
    assert any(e.event_type.name == "ABANDONED_OBJECT" for e in events)


def test_reset(tmp_path: Path) -> None:
    mon = ObjectMonitor(_config(tmp_path))
    mon.process([_bag(5, 200, 200)], [], now=0.0)
    mon.reset()
    assert isinstance(mon.process([], [], now=1.0), list)
    assert mon.process([], [], now=10.0) == []  # nothing tracked after reset
