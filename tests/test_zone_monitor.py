import threading
from pathlib import Path

from core.config import load_config
from plugins.base import Detection
from storage.database import Zone
from zones.monitor import ZoneEvent, ZoneMonitor

_SQUARE = [(0, 0), (100, 0), (100, 100), (0, 100)]


def _config(tmp_path: Path, loiter: float = 5.0):
    p = tmp_path / "c.yaml"
    p.write_text(f"zones:\n  loiter_seconds: {loiter}\n  expire_seconds: 5.0\n"
                 "  tailgate_seconds: 2.0\n  queue_min: 3\n", encoding="utf-8")
    return load_config(p)


def _zone(zid: int, ztype: str, poly, loiter=None) -> Zone:
    return Zone(id=zid, source_id=1, name=f"z{zid}", type=ztype, polygon=poly,
                loiter_seconds=loiter, created_at=0.0)


def _zone_ad(zid, ztype, poly, allowed=None, loiter=None):
    return Zone(id=zid, source_id=1, name=f"z{zid}", type=ztype, polygon=poly,
                loiter_seconds=loiter, allowed_direction=allowed, created_at=0.0)


def _person(track_id: int, cx: int, cy: int) -> Detection:
    # bottom-center of the bbox is (cx, cy)
    return Detection(label="person", confidence=0.9, bbox=(cx - 5, cy - 20, cx + 5, cy),
                     category="person", track_id=track_id)


def test_restricted_entry_event(tmp_path: Path) -> None:
    mon = ZoneMonitor(_config(tmp_path))
    mon.set_zones([_zone(1, "restricted", _SQUARE)])
    assert mon.process([_person(7, 200, 200)], now=1.0, source_id=1) == []  # outside
    events = mon.process([_person(7, 50, 50)], now=2.0, source_id=1)        # entered
    assert len(events) == 1 and events[0].event_type.name == "RESTRICTED"
    assert isinstance(events[0], ZoneEvent)
    assert mon.occupancy()[1] == 1
    assert mon.counts()[1][0] == 1  # one entry


def test_loitering_once_per_visit(tmp_path: Path) -> None:
    mon = ZoneMonitor(_config(tmp_path, loiter=5.0))
    mon.set_zones([_zone(1, "lobby", _SQUARE)])
    mon.process([_person(7, 50, 50)], now=0.0, source_id=1)     # enter
    assert mon.process([_person(7, 50, 50)], now=3.0, source_id=1) == []      # dwell 3 < 5
    ev = mon.process([_person(7, 50, 50)], now=6.0, source_id=1)              # dwell 6 >= 5
    assert len(ev) == 1 and ev[0].event_type.name == "LOITERING"
    assert mon.process([_person(7, 50, 50)], now=9.0, source_id=1) == []      # once per visit


def test_exit_count(tmp_path: Path) -> None:
    mon = ZoneMonitor(_config(tmp_path))
    mon.set_zones([_zone(1, "lobby", _SQUARE)])
    mon.process([_person(7, 50, 50)], now=0.0, source_id=1)     # inside
    mon.process([_person(7, 200, 200)], now=1.0, source_id=1)   # outside -> exit
    assert mon.counts()[1][1] == 1  # one exit
    assert mon.occupancy()[1] == 0


def test_line_crossing(tmp_path: Path) -> None:
    mon = ZoneMonitor(_config(tmp_path))
    mon.set_zones([_zone(1, "line", [(50, 0), (50, 100)])])   # vertical line at x=50
    mon.process([_person(7, 20, 50)], now=0.0, source_id=1)   # left of line, no prev
    ev = mon.process([_person(7, 80, 50)], now=1.0, source_id=1)  # crossed to right
    assert len(ev) == 1 and ev[0].event_type.name == "LINE_CROSS"
    assert "direction" in ev[0].metadata


def test_snapshot_and_reset(tmp_path: Path) -> None:
    mon = ZoneMonitor(_config(tmp_path))
    mon.set_zones([_zone(1, "lobby", _SQUARE)])
    mon.process([_person(7, 50, 50)], now=0.0, source_id=1)
    snap = mon.snapshot()
    assert len(snap) == 1 and snap[0].occupancy == 1 and snap[0].zone_id == 1
    mon.reset()
    assert mon.occupancy().get(1, 0) == 0


def test_concurrent_set_zones_and_process(tmp_path: Path) -> None:
    mon = ZoneMonitor(_config(tmp_path))
    zones = [_zone(1, "lobby", _SQUARE)]
    mon.set_zones(zones)
    errors: list[Exception] = []
    stop = threading.Event()

    def worker() -> None:
        try:
            while not stop.is_set():
                mon.process([_person(7, 50, 50)], now=1.0, source_id=1)
                mon.snapshot()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t = threading.Thread(target=worker)
    t.start()
    for _ in range(300):
        mon.set_zones(zones)   # clears state dicts mid-iteration on the other thread
    stop.set()
    t.join(timeout=5)
    assert errors == []


def test_default_config_has_zones_section() -> None:
    from core.config import Config
    cfg = Config(Path("config/default.yaml"))
    assert cfg.get("zones.loiter_seconds") == 30.0
    assert cfg.get("zones.expire_seconds") == 5.0


def test_wrong_direction(tmp_path: Path) -> None:
    mon = ZoneMonitor(_config(tmp_path))
    mon.set_zones([_zone_ad(1, "line", [(50, 0), (50, 100)], allowed="b->a")])
    mon.process([_person(7, 20, 50)], now=0.0, source_id=1)          # left, no prev
    ev = mon.process([_person(7, 80, 50)], now=1.0, source_id=1)     # left->right = "b->a" = allowed
    assert [e.event_type.name for e in ev] == ["LINE_CROSS"]
    mon.process([_person(9, 80, 50)], now=2.0, source_id=1)          # right, no prev
    ev2 = mon.process([_person(9, 20, 50)], now=3.0, source_id=1)    # right->left = "a->b" != allowed -> wrong
    assert "WRONG_DIRECTION" in [e.event_type.name for e in ev2]


def test_tailgating(tmp_path: Path) -> None:
    mon = ZoneMonitor(_config(tmp_path))
    mon.set_zones([_zone_ad(1, "line", [(50, 0), (50, 100)])])
    mon.process([_person(7, 20, 50)], now=0.0, source_id=1)
    mon.process([_person(7, 80, 50)], now=0.5, source_id=1)          # track 7 crosses
    mon.process([_person(9, 20, 60)], now=0.6, source_id=1)
    ev = mon.process([_person(9, 80, 60)], now=1.0, source_id=1)     # track 9 crosses within 2s
    assert any(e.event_type.name == "TAILGATING" for e in ev)


def test_queue(tmp_path: Path) -> None:
    mon = ZoneMonitor(_config(tmp_path))       # queue_min default 3 (config below)
    mon.set_zones([_zone_ad(1, "queue", _SQUARE)])
    dets = [_person(t, 50, 50) for t in (1, 2, 3)]
    ev = mon.process(dets, now=0.0, source_id=1)
    assert any(e.event_type.name == "QUEUE" and e.metadata.get("length") == 3
               for e in ev)
    # not re-fired while still full
    assert not any(e.event_type.name == "QUEUE"
                   for e in mon.process(dets, now=1.0, source_id=1))
