import time
from pathlib import Path
from typing import Iterator

import pytest

from events.types import Event, EventType
from storage.database import Database


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "test.db")
    yield d
    d.close()


def test_source_crud(db: Database) -> None:
    sid = db.add_source("Sahil", "http://example.com/a.mjpg")
    db.add_source("Benim Ev", "http://example.com/b.mjpg")
    sources = db.list_sources()
    assert [s.name for s in sources] == ["Sahil", "Benim Ev"]
    db.update_source(sid, "Sahil 2", "http://example.com/c.mjpg")
    assert db.list_sources()[0].name == "Sahil 2"
    db.delete_source(sid)
    assert len(db.list_sources()) == 1


def test_touch_source_sets_last_used(db: Database) -> None:
    sid = db.add_source("Sahil", "http://x/a.mjpg")
    assert db.list_sources()[0].last_used_at is None
    db.touch_source(sid)
    assert db.list_sources()[0].last_used_at is not None


def test_event_roundtrip(db: Database) -> None:
    sid = db.add_source("Sahil", "http://x/a.mjpg")
    ev = Event(
        type=EventType.PERSON, timestamp=time.time(), source_id=sid,
        label="person", confidence=0.9, bbox=(10, 20, 110, 220),
        metadata={"track_id": 7},
    )
    db.add_event(ev, snapshot_path="data/snapshots/x.jpg")
    stored = db.list_events()
    assert len(stored) == 1
    assert stored[0].type == "PERSON"
    assert stored[0].bbox == (10, 20, 110, 220)
    assert stored[0].metadata == {"track_id": 7}
    assert stored[0].snapshot_path == "data/snapshots/x.jpg"


def test_list_events_since_and_count(db: Database) -> None:
    now = time.time()
    for i in range(5):
        db.add_event(Event(type=EventType.MOTION, timestamp=now - 100 + i,
                           source_id=None, label="motion"))
    assert db.count_events_since(now - 98.5) == 3
    assert len(db.list_events(limit=2)) == 2
    assert len(db.list_events(since=now - 98.5)) == 3


def test_settings(db: Database) -> None:
    assert db.get_setting("missing") is None
    assert db.get_setting("missing", "d") == "d"
    db.set_setting("last_source_id", "3")
    db.set_setting("last_source_id", "4")
    assert db.get_setting("last_source_id") == "4"


def test_thread_safety(db: Database) -> None:
    import threading

    def writer() -> None:
        for _ in range(50):
            db.add_event(Event(type=EventType.MOTION, timestamp=time.time(),
                               source_id=None, label="motion"))

    threads = [threading.Thread(target=writer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert db.count_events_since(0) == 200


def test_recording_crud(db: Database) -> None:
    from storage.database import Recording

    rid = db.add_recording(
        kind="clip", path="data/recordings/a.mp4", start_ts=100.0, end_ts=110.0,
        mode="event", trigger="PERSON", source_id=1, size_bytes=2048,
    )
    db.add_recording(kind="timelapse", path="data/timelapse/t.mp4", start_ts=50.0,
                     end_ts=90.0, mode="timelapse", trigger=None, source_id=1,
                     size_bytes=4096)
    recs = db.list_recordings()
    assert len(recs) == 2
    assert isinstance(recs[0], Recording)
    assert recs[0].start_ts == 100.0  # newest (ts DESC) first
    assert db.list_recordings(kind="timelapse")[0].path == "data/timelapse/t.mp4"
    assert db.total_recordings_size() == 2048 + 4096
    assert db.count_recordings() == 2
    older = db.list_recordings_older_than(95.0)
    assert [r.kind for r in older] == ["timelapse"]  # start_ts 50 < 95
    db.delete_recording(rid)
    assert len(db.list_recordings()) == 1
    assert db.total_recordings_size() == 4096
    assert db.count_recordings() == 1


def test_statistics_and_search(db: Database) -> None:
    from storage.database import DailyStat

    day = 86400.0
    d0 = 100 * day  # a day_start
    # search_events over events table
    db.add_event(Event(type=EventType.PERSON, timestamp=d0 + 10, source_id=1,
                       label="person"))
    db.add_event(Event(type=EventType.VEHICLE, timestamp=d0 + 20, source_id=1,
                       label="araba"))
    db.add_event(Event(type=EventType.PERSON, timestamp=d0 + 30, source_id=2,
                       label="person"))
    db.add_event(Event(type=EventType.MOTION, timestamp=d0 - day, source_id=None,
                       label="motion"))  # previous day, no source

    persons = db.search_events(d0, d0 + day, types=["PERSON"])
    assert [e.type for e in persons] == ["PERSON", "PERSON"]
    src1 = db.search_events(d0, d0 + day, source_id=1)
    assert len(src1) == 2
    counts = db.event_type_counts(d0, d0 + day)
    assert counts == {"PERSON": 2, "VEHICLE": 1}

    rollup = db.daily_rollup_counts(d0, d0 + day)
    # (source_id, type, count); source 1: PERSON 1, VEHICLE 1; source 2: PERSON 1
    assert (1, "PERSON", 1) in rollup
    assert (2, "PERSON", 1) in rollup
    assert (1, "VEHICLE", 1) in rollup

    # upsert idempotent + update
    db.upsert_daily_stat(d0, 1, "PERSON", 5)
    db.upsert_daily_stat(d0, 1, "PERSON", 8)  # same key -> update
    db.upsert_daily_stat(d0, 2, "PERSON", 3)
    stats = db.daily_stats(d0, d0 + day)
    person1 = [s for s in stats if s.source_id == 1 and s.event_type == "PERSON"]
    assert len(person1) == 1 and person1[0].count == 8
    assert isinstance(stats[0], DailyStat)
