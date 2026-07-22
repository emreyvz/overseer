from pathlib import Path
from typing import Iterator

import pytest

from events.types import Event, EventType
from forensic.search import ForensicSearchService, summarize_tracklet
from storage.database import Database


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "svc.db")
    yield d
    d.close()


def _tracklet(db: Database, ts: float, upper: str, acc: list[str]) -> int:
    tid = db.add_tracklet(1, int(ts), ts, ts)
    db.update_tracklet_attributes(
        tid, height_band="tall", build="medium", upper_color=upper, lower_color="black",
        clothing_type=None, accessories=acc, attr_conf=1.0, snapshot_path=None,
        last_ts=ts, now=ts)
    return tid


def test_attribute_only_search(db: Database) -> None:
    _tracklet(db, 100.0, "red", ["backpack"])
    _tracklet(db, 101.0, "blue", [])
    svc = ForensicSearchService(db)
    res = svc.search("red", filters={"start": 0.0, "end": 1e12})
    assert [h.kind for h in res.hits] == ["tracklet"]
    assert res.hits[0].type == "TRACKLET"


def test_event_only_search(db: Database) -> None:
    db.add_event(Event(type=EventType.VEHICLE, timestamp=200.0, source_id=1,
                       label="araba"))
    svc = ForensicSearchService(db)
    res = svc.search("vehicle", filters={"start": 0.0, "end": 1e12})
    assert [h.kind for h in res.hits] == ["event"]
    assert res.hits[0].type == "VEHICLE"


def test_mixed_sorted_ts_desc(db: Database) -> None:
    _tracklet(db, 100.0, "red", [])
    db.add_event(Event(type=EventType.VEHICLE, timestamp=300.0, source_id=1,
                       label="araba"))
    svc = ForensicSearchService(db)
    res = svc.search("red vehicle", filters={"start": 0.0, "end": 1e12})
    assert [h.ts for h in res.hits] == [300.0, 100.0]  # newest first


def test_deferred_and_empty(db: Database) -> None:
    svc = ForensicSearchService(db)
    res = svc.search("running zzz", filters={"start": 0.0, "end": 1e12})
    assert res.hits == []
    assert "running" in res.deferred_terms
    assert "zzz" in res.unmatched


def test_no_text_no_filters(db: Database) -> None:
    svc = ForensicSearchService(db)
    assert svc.search().hits == []


def test_summarize() -> None:
    from storage.database import Tracklet
    t = Tracklet(id=1, source_id=1, track_id=2, first_ts=1.0, last_ts=1.0, obs_count=1,
                 snapshot_path=None, height_band="tall", build="medium",
                 upper_color="red", lower_color="blue", clothing_type=None,
                 accessories=["backpack"], attr_conf=1.0, pinned=0,
                 created_at=1.0, updated_at=1.0)
    s = summarize_tracklet(t)
    assert "red" in s and "blue" in s and "tall" in s and "backpack" in s
