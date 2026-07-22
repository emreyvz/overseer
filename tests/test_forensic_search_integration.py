from pathlib import Path
from typing import Iterator

import pytest

from events.types import Event, EventType
from forensic.search import ForensicSearchService
from storage.database import Database


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "e.db")
    yield d
    d.close()


def test_end_to_end_unified_search(db: Database) -> None:
    tid = db.add_tracklet(1, 7, 100.0, 100.0)
    db.update_tracklet_attributes(
        tid, height_band="tall", build="medium", upper_color="red",
        lower_color="black", clothing_type=None, accessories=["backpack"],
        attr_conf=1.0, snapshot_path=None, last_ts=100.0, now=100.0)
    db.add_event(Event(type=EventType.VEHICLE, timestamp=300.0, source_id=1,
                       label="araba"))
    svc = ForensicSearchService(db)
    res = svc.search("red backpack vehicle",
                     filters={"start": 0.0, "end": 1e12})
    kinds = [h.kind for h in res.hits]
    assert kinds == ["event", "tracklet"]  # event ts 300 newer than tracklet 100
    assert res.hits[1].label.startswith("red")
