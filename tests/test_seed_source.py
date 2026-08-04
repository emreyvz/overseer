"""A fresh install seeds the public demo cameras so the map is not empty on first run."""
from pathlib import Path

from storage.database import DEMO_SOURCES, Database


def test_fresh_db_seeds_demo_cameras_with_locations(tmp_path: Path) -> None:
    db = Database(tmp_path / "fresh.db")
    try:
        assert db.list_sources() == []          # nothing before seeding
        db.seed_default_source()
        sources = db.list_sources()
        assert len(sources) == len(DEMO_SOURCES)
        assert [s.name for s in sources] == ["Street", "Airport", "Hotel", "Dock"]
        for cam in sources:
            assert cam.url.startswith("http")
            assert cam.map_x is not None and cam.map_y is not None   # placed on the map
        # spread out, not stacked on one pin
        assert len({(s.map_x, s.map_y) for s in sources}) == len(sources)
    finally:
        db.close()


def test_seed_is_idempotent_and_never_touches_existing(tmp_path: Path) -> None:
    db = Database(tmp_path / "used.db")
    try:
        db.add_source("My Camera", "http://example/stream.mjpg")
        db.seed_default_source()                # must NOT add the demo cameras
        names = [s.name for s in db.list_sources()]
        assert names == ["My Camera"]
        db.seed_default_source()                # second call is a no-op
        assert [s.name for s in db.list_sources()] == ["My Camera"]
    finally:
        db.close()


def test_fresh_seed_then_reseed_adds_nothing(tmp_path: Path) -> None:
    db = Database(tmp_path / "twice.db")
    try:
        db.seed_default_source()
        db.seed_default_source()
        assert len(db.list_sources()) == len(DEMO_SOURCES)
    finally:
        db.close()


def test_install_with_only_the_old_demo_camera_is_topped_up(tmp_path: Path) -> None:
    """Installs seeded when there was a single demo camera get the new ones."""
    path = tmp_path / "v1.db"
    db = Database(path)
    try:
        name, url, lat, lng = DEMO_SOURCES[0]
        db.update_source(db.add_source(name, url), name, url, lat, lng)
        db.set_setting("default_source_seeded", "1")   # what the old seed left behind
    finally:
        db.close()

    db = Database(path)
    try:
        db.seed_default_source()
        assert [s.name for s in db.list_sources()] == ["Street", "Airport", "Hotel", "Dock"]
        db.seed_default_source()                       # and stays put afterwards
        assert len(db.list_sources()) == len(DEMO_SOURCES)
    finally:
        db.close()


def test_top_up_skips_installs_that_have_operator_cameras(tmp_path: Path) -> None:
    path = tmp_path / "mixed.db"
    db = Database(path)
    try:
        name, url, lat, lng = DEMO_SOURCES[0]
        db.update_source(db.add_source(name, url), name, url, lat, lng)
        db.add_source("Loading Bay", "rtsp://10.0.0.9/stream")
        db.set_setting("default_source_seeded", "1")
    finally:
        db.close()

    db = Database(path)
    try:
        db.seed_default_source()
        assert [s.name for s in db.list_sources()] == ["Street", "Loading Bay"]
    finally:
        db.close()
