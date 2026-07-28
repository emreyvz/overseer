from pathlib import Path

from core.config import load_config
from objects.monitor import ObjectEvent, ObjectMonitor
from plugins.base import Detection


def _config(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "objects:\n  buffer_size: 300\n  expire_seconds: 30.0\n  abandon_seconds: 3.0\n"
        "  obj_eps: 20.0\n  owner_distance: 100.0\n  stable_seconds: 2.0\n"
        "  removed_seconds: 3.0\n  unattended_seconds: 3.0\n  owner_min_frames: 2\n",
        encoding="utf-8")
    return load_config(p)


def _bag(tid: int, cx: int, cy: int, label: str = "backpack") -> Detection:
    return Detection(label, 0.9, (cx - 10, cy - 10, cx + 10, cy + 10),
                     "accessory", track_id=tid)


def _person(cx: int, cy: int) -> Detection:
    return Detection("person", 0.9, (cx - 15, cy - 60, cx + 15, cy), "person", track_id=99)


def _abandoned(events) -> list:
    return [e for e in events if e.event_type.name == "ABANDONED_OBJECT"]


def test_abandoned_after_owner_leaves(tmp_path: Path) -> None:
    mon = ObjectMonitor(_config(tmp_path))
    events = []
    for i in range(3):        # owner brings the bag and stays a few frames
        events += mon.process([_bag(5, 200, 200)], [_person(210, 250)], now=float(i))
    for i in range(3, 8):     # owner leaves; the bag stays put
        events += mon.process([_bag(5, 200, 200)], [], now=float(i))
    ab = _abandoned(events)
    assert ab and isinstance(ab[0], ObjectEvent)


def test_static_object_never_abandoned(tmp_path: Path) -> None:
    # the false-positive case: a stationary bag NObody ever tended (a fixture on a rack)
    mon = ObjectMonitor(_config(tmp_path))
    events = []
    for i in range(12):
        events += mon.process([_bag(5, 200, 200)], [], now=float(i))
    assert not _abandoned(events)


def test_not_abandoned_while_owner_present(tmp_path: Path) -> None:
    mon = ObjectMonitor(_config(tmp_path))
    events = []
    for i in range(10):       # owner stands next to the bag the whole time
        events += mon.process([_bag(5, 200, 200)], [_person(210, 250)], now=float(i))
    assert not _abandoned(events)


def test_passerby_does_not_count_as_owner(tmp_path: Path) -> None:
    # someone walks past the bag for a single frame -> not "someone's" -> not abandoned
    mon = ObjectMonitor(_config(tmp_path))
    events = []
    for i in range(12):
        persons = [_person(210, 250)] if i == 1 else []
        events += mon.process([_bag(5, 200, 200)], persons, now=float(i))
    assert not _abandoned(events)


def test_non_luggage_class_ignored(tmp_path: Path) -> None:
    mon = ObjectMonitor(_config(tmp_path))
    events = []
    for i in range(3):
        events += mon.process([_bag(5, 200, 200, label="umbrella")], [_person(210, 250)], now=float(i))
    for i in range(3, 8):
        events += mon.process([_bag(5, 200, 200, label="umbrella")], [], now=float(i))
    assert not _abandoned(events)


def test_moving_object_not_abandoned(tmp_path: Path) -> None:
    mon = ObjectMonitor(_config(tmp_path))
    events = []
    for i in range(3):
        events += mon.process([_bag(5, 200, 200)], [_person(210, 250)], now=float(i))
    for i in range(3, 10):    # owner gone, but the bag is being carried (moving)
        events += mon.process([_bag(5, 200 + i * 30, 200)], [], now=float(i))
    assert not _abandoned(events)


def test_removed(tmp_path: Path) -> None:
    mon = ObjectMonitor(_config(tmp_path))
    for i in range(3):
        mon.process([_bag(5, 200, 200)], [_person(210, 250)], now=float(i))  # established
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


def test_reset(tmp_path: Path) -> None:
    mon = ObjectMonitor(_config(tmp_path))
    mon.process([_bag(5, 200, 200)], [], now=0.0)
    mon.reset()
    assert mon.process([], [], now=10.0) == []
