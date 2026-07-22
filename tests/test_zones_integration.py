from pathlib import Path

from core.config import load_config
from plugins.base import Detection
from storage.database import Zone
from zones.monitor import ZoneMonitor


def _config(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("zones:\n  loiter_seconds: 3.0\n  expire_seconds: 5.0\n",
                 encoding="utf-8")
    return load_config(p)


def test_restricted_then_loiter_flow(tmp_path: Path) -> None:
    mon = ZoneMonitor(_config(tmp_path))
    mon.set_zones([Zone(id=1, source_id=1, name="Kasa", type="restricted",
                        polygon=[(0, 0), (100, 0), (100, 100), (0, 100)],
                        loiter_seconds=None, created_at=0.0)])

    def person(cx: int, cy: int) -> Detection:
        return Detection("person", 0.9, (cx - 5, cy - 20, cx + 5, cy), "person",
                         track_id=7)

    assert mon.process([person(200, 200)], 0.0, 1) == []            # outside
    e1 = mon.process([person(50, 50)], 1.0, 1)                      # enter -> restricted
    assert [e.event_type.name for e in e1] == ["RESTRICTED"]
    e2 = mon.process([person(50, 50)], 5.0, 1)                      # dwell 4 >= 3 -> loiter
    assert [e.event_type.name for e in e2] == ["LOITERING"]
    assert mon.snapshot()[0].occupancy == 1
