"""A fresh install seeds one public demo camera so the map is not empty on first run."""
from pathlib import Path

from storage.database import Database


def test_fresh_db_seeds_one_camera_with_location(tmp_path: Path) -> None:
    db = Database(tmp_path / "fresh.db")
    try:
        assert db.list_sources() == []          # nothing before seeding
        db.seed_default_source()
        sources = db.list_sources()
        assert len(sources) == 1
        cam = sources[0]
        assert cam.name == "Street"
        assert cam.url.startswith("http")
        assert cam.map_x is not None and cam.map_y is not None   # placed on the map
    finally:
        db.close()


def test_seed_is_idempotent_and_never_touches_existing(tmp_path: Path) -> None:
    db = Database(tmp_path / "used.db")
    try:
        db.add_source("My Camera", "http://example/stream.mjpg")
        db.seed_default_source()                # must NOT add the demo camera
        names = [s.name for s in db.list_sources()]
        assert names == ["My Camera"]
        db.seed_default_source()                # second call is a no-op
        assert [s.name for s in db.list_sources()] == ["My Camera"]
    finally:
        db.close()
