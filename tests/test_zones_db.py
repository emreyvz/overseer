from pathlib import Path
from typing import Iterator

import pytest

from storage.database import Database, Zone


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "z.db")
    yield d
    d.close()


def test_zone_crud(db: Database) -> None:
    poly = [(0, 0), (10, 0), (10, 10), (0, 10)]
    zid = db.add_zone(1, "Input", "entrance", poly, None)
    db.add_zone(2, "Yasak", "restricted", [(1, 1), (2, 2), (3, 1)], 0.0)
    zones = db.list_zones(source_id=1)
    assert len(zones) == 1
    assert isinstance(zones[0], Zone)
    assert zones[0].polygon == poly           # json round-trip -> list of tuples
    assert zones[0].loiter_seconds is None
    assert len(db.list_zones()) == 2          # all sources
    db.update_zone(zid, "Input 2", "lobby", 45.0)
    updated = db.list_zones(source_id=1)[0]
    assert updated.name == "Input 2" and updated.type == "lobby"
    assert updated.loiter_seconds == 45.0
    db.delete_zone(zid)
    assert db.list_zones(source_id=1) == []


def test_zone_allowed_direction_roundtrip(db: Database) -> None:
    poly = [(0, 0), (100, 50)]
    zid = db.add_zone(1, "Door", "line", poly, None, allowed_direction="a->b")
    z = db.list_zones(source_id=1)[0]
    assert z.allowed_direction == "a->b"
    db.update_zone(zid, "Door", "line", None, allowed_direction="b->a")
    assert db.list_zones(source_id=1)[0].allowed_direction == "b->a"


def test_zone_migration_adds_column(tmp_path: Path) -> None:
    import sqlite3
    # Simulate an OLD db: zones table without allowed_direction.
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE zones (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER,"
        " name TEXT NOT NULL, type TEXT NOT NULL, polygon TEXT NOT NULL,"
        " loiter_seconds REAL, created_at REAL NOT NULL)")
    conn.execute("INSERT INTO zones (name, type, polygon, created_at) VALUES"
                 " ('X','lobby','[[0,0],[1,1],[2,0]]',0.0)")
    conn.commit()
    conn.close()
    db = Database(path)  # opening runs the migration
    try:
        z = db.list_zones()[0]
        assert z.name == "X" and z.allowed_direction is None  # existing row preserved
        db.add_zone(1, "Y", "line", [(0, 0), (5, 5)], None, allowed_direction="a->b")
        assert db.list_zones(source_id=1)[0].allowed_direction == "a->b"
    finally:
        db.close()
