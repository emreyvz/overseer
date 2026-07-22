# tests/test_search_tracklets.py
from pathlib import Path
from typing import Iterator

import pytest

from storage.database import Database


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "s.db")
    yield d
    d.close()


def _mk(db: Database, *, track_id: int, ts: float, upper: str, lower: str,
        height: str, build: str, acc: list[str], source_id: int = 1) -> int:
    tid = db.add_tracklet(source_id, track_id, ts, ts)
    db.update_tracklet_attributes(
        tid, height_band=height, build=build, upper_color=upper, lower_color=lower,
        clothing_type=None, accessories=acc, attr_conf=1.0, snapshot_path=None,
        last_ts=ts, now=ts)
    return tid


def test_color_matches_upper_or_lower(db: Database) -> None:
    a = _mk(db, track_id=1, ts=100.0, upper="red", lower="siyah",
            height="uzun", build="orta", acc=[])
    b = _mk(db, track_id=2, ts=101.0, upper="mavi", lower="red",
            height="orta", build="ince", acc=[])
    _mk(db, track_id=3, ts=102.0, upper="green", lower="siyah",
        height="short", build="broad", acc=[])
    ids = {t.id for t in db.search_tracklets(colors=["red"])}
    assert ids == {a, b}  # matched on upper (a) and lower (b)


def test_accessory_like_and_height(db: Database) -> None:
    a = _mk(db, track_id=1, ts=100.0, upper="mavi", lower="siyah", height="uzun",
            build="orta", acc=["backpack"])
    _mk(db, track_id=2, ts=101.0, upper="mavi", lower="siyah", height="short",
        build="orta", acc=["umbrella"])
    got = db.search_tracklets(accessories=["backpack"], height_bands=["uzun"])
    assert [t.id for t in got] == [a]


def test_time_source_limit_and_order(db: Database) -> None:
    for i in range(4):
        _mk(db, track_id=i, ts=100.0 + i, upper="mavi", lower="siyah",
            height="orta", build="orta", acc=[], source_id=1 if i < 3 else 2)
    got = db.search_tracklets(source_id=1, start=100.5, end=200.0, limit=2)
    # source 1, ts in [100.5,200): track 1,2 (ts 101,102); newest first, limit 2
    assert [t.last_ts for t in got] == [102.0, 101.0]
