from pathlib import Path

from core.config import load_config
from plugins.base import Detection
from objects.monitor import ObjectMonitor
from pose.monitor import PoseMonitor


def _pose_config(tmp_path: Path):
    p = tmp_path / "p.yaml"
    p.write_text(
        "pose:\n  buffer_size: 30\n  expire_seconds: 5.0\n  standing_aspect: 1.2\n"
        "  fallen_aspect: 0.8\n  crowd_min: 3\n  fight_distance: 60.0\n"
        "  fight_motion: 50.0\n", encoding="utf-8")
    return load_config(p)


def _obj_config(tmp_path: Path):
    p = tmp_path / "o.yaml"
    p.write_text(
        "objects:\n  buffer_size: 300\n  expire_seconds: 30.0\n  abandon_seconds: 3.0\n"
        "  obj_eps: 20.0\n  owner_distance: 100.0\n  stable_seconds: 2.0\n"
        "  removed_seconds: 3.0\n  unattended_seconds: 3.0\n  owner_min_frames: 2\n",
        encoding="utf-8")
    return load_config(p)


def test_pose_falling_and_crowd(tmp_path: Path) -> None:
    mon = PoseMonitor(_pose_config(tmp_path))

    def standing(t, cx):
        return Detection("person", 0.9, (cx - 15, cx * 0, cx + 15, 60), "person",
                         track_id=t)

    def fallen(t):
        return Detection("person", 0.9, (0, 45, 80, 60), "person", track_id=t)

    events = []
    events += mon.process([standing(1, 20)], 0.0)
    events += mon.process([fallen(1)], 1.0)                    # 1 falls
    events += mon.process([standing(1, 20), standing(2, 60),
                           standing(3, 100)], 2.0)             # 3 -> crowd
    names = [e.event_type.name for e in events]
    assert "FALLING" in names and "CROWDING" in names


def test_object_abandoned(tmp_path: Path) -> None:
    mon = ObjectMonitor(_obj_config(tmp_path))
    bag = Detection("backpack", 0.9, (190, 190, 210, 210), "accessory",
                    track_id=5)
    owner = Detection("person", 0.9, (195, 200, 225, 300), "person", track_id=7)
    events = []
    for i in range(3):
        events += mon.process([bag], [owner], now=float(i))    # owner brings it, stays
    for i in range(3, 8):
        events += mon.process([bag], [], now=float(i))         # owner leaves; bag stays put
    assert any(e.event_type.name == "ABANDONED_OBJECT" for e in events)
