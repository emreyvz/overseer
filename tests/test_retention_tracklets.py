from pathlib import Path
from typing import Iterator

import pytest

from core.config import load_config
from storage.database import Database
from storage.retention import RetentionPolicy


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "r.db")
    yield d
    d.close()


def test_tracklet_prune_by_ttl(db: Database, tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        "retention:\n  enabled: true\n  max_age_days: 3650\n"
        "forensic:\n  retention_days: 1\n",
        encoding="utf-8",
    )
    config = load_config(p)
    now = 1_000_000.0
    day = 86400.0
    old = db.add_tracklet(1, 1, now - 5 * day, now - 5 * day)   # created 5d ago
    pinned = db.add_tracklet(1, 2, now - 5 * day, now - 5 * day)
    db.set_tracklet_pinned(pinned, 1)
    db.add_tracklet(1, 3, now - 0.1 * day, now - 0.1 * day)     # fresh
    summary = RetentionPolicy(config, db, tmp_path / "snaps").run_once(now)
    assert summary.tracklets_deleted == 1
    assert db.get_tracklet(old) is None
    assert db.get_tracklet(pinned) is not None
    assert db.count_tracklets() == 2


def make_crop(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xff\xd8\xff\xd9")  # minimal jpeg-ish bytes, content unimportant
    return path


def _config(tmp_path: Path, name: str, text: str):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return load_config(p)


def test_tracklet_crop_unlinked_on_ttl_prune(db: Database, tmp_path: Path) -> None:
    # retention.enabled=true, forensic.retention_days=1: an old unpinned tracklet's
    # crop JPEG must be unlinked from disk along with its DB row; a pinned tracklet's
    # crop and row must survive.
    config = _config(
        tmp_path, "c.yaml",
        "retention:\n  enabled: true\n  max_age_days: 3650\n"
        "forensic:\n  retention_days: 1\n",
    )
    now = 1_000_000.0
    day = 86400.0

    unpinned_crop = make_crop(tmp_path / "snaps" / "unpinned.jpg")
    unpinned = db.add_tracklet(1, 1, now - 5 * day, now - 5 * day)
    db.update_tracklet_attributes(
        unpinned, height_band=None, build=None, upper_color=None, lower_color=None,
        clothing_type=None, accessories=[], attr_conf=None,
        snapshot_path=str(unpinned_crop), last_ts=now - 5 * day, now=now - 5 * day,
    )

    pinned_crop = make_crop(tmp_path / "snaps" / "pinned.jpg")
    pinned = db.add_tracklet(1, 2, now - 5 * day, now - 5 * day)
    db.update_tracklet_attributes(
        pinned, height_band=None, build=None, upper_color=None, lower_color=None,
        clothing_type=None, accessories=[], attr_conf=None,
        snapshot_path=str(pinned_crop), last_ts=now - 5 * day, now=now - 5 * day,
    )
    db.set_tracklet_pinned(pinned, 1)

    RetentionPolicy(config, db, tmp_path / "snaps").run_once(now)

    assert not unpinned_crop.exists()
    assert db.get_tracklet(unpinned) is None
    assert pinned_crop.exists()
    assert db.get_tracklet(pinned) is not None


def test_tracklet_prune_runs_when_media_retention_disabled(
    db: Database, tmp_path: Path
) -> None:
    # Media retention.enabled=false must NOT gate the forensic tracklet TTL prune.
    config = _config(
        tmp_path, "c.yaml",
        "retention:\n  enabled: false\n"
        "forensic:\n  retention_days: 1\n",
    )
    now = 1_000_000.0
    day = 86400.0
    old = db.add_tracklet(1, 1, now - 5 * day, now - 5 * day)

    summary = RetentionPolicy(config, db, tmp_path / "snaps").run_once(now)

    assert summary.tracklets_deleted == 1
    assert db.get_tracklet(old) is None


def test_session_only_no_periodic_prune(db: Database, tmp_path: Path) -> None:
    # forensic.retention_days=0 (session-only) must not periodically prune
    # still-active tracklets mid-session; only shutdown purge should do that.
    config = _config(
        tmp_path, "c.yaml",
        "retention:\n  enabled: true\n  max_age_days: 3650\n"
        "forensic:\n  retention_days: 0\n",
    )
    now = 1_000_000.0
    day = 86400.0
    old = db.add_tracklet(1, 1, now - 30 * day, now - 30 * day)

    summary = RetentionPolicy(config, db, tmp_path / "snaps").run_once(now)

    assert summary.tracklets_deleted == 0
    assert db.get_tracklet(old) is not None


def test_purge_session_tracklets(db: Database, tmp_path: Path) -> None:
    now = 1_000_000.0

    unpinned_crop = make_crop(tmp_path / "snaps" / "unpinned.jpg")
    unpinned = db.add_tracklet(1, 1, now, now)
    db.update_tracklet_attributes(
        unpinned, height_band=None, build=None, upper_color=None, lower_color=None,
        clothing_type=None, accessories=[], attr_conf=None,
        snapshot_path=str(unpinned_crop), last_ts=now, now=now,
    )

    pinned_crop = make_crop(tmp_path / "snaps" / "pinned.jpg")
    pinned = db.add_tracklet(1, 2, now, now)
    db.update_tracklet_attributes(
        pinned, height_band=None, build=None, upper_color=None, lower_color=None,
        clothing_type=None, accessories=[], attr_conf=None,
        snapshot_path=str(pinned_crop), last_ts=now, now=now,
    )
    db.set_tracklet_pinned(pinned, 1)

    session_config = _config(
        tmp_path, "session.yaml", "forensic:\n  retention_days: 0\n",
    )
    purged = RetentionPolicy(session_config, db, tmp_path / "snaps").purge_session_tracklets()

    assert purged == 1
    assert not unpinned_crop.exists()
    assert db.get_tracklet(unpinned) is None
    assert pinned_crop.exists()
    assert db.get_tracklet(pinned) is not None

    # With forensic.retention_days > 0, purge_session_tracklets() is a no-op.
    ttl_config = _config(
        tmp_path, "ttl.yaml", "forensic:\n  retention_days: 1\n",
    )
    noop = RetentionPolicy(ttl_config, db, tmp_path / "snaps").purge_session_tracklets()
    assert noop == 0
    assert db.get_tracklet(pinned) is not None


def make_file(path: Path, size: int, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    import os
    os.utime(path, (mtime, mtime))


def test_pinned_tracklet_crop_survives_media_sweep(db: Database, tmp_path: Path) -> None:
    # The legacy media-retention snapshot sweep (_prune_old_snapshots) globs the
    # whole snapshot tree by mtime with no pinned check. Tracklet crops live in
    # that same tree, so a pinned tracklet's crop must be exempted explicitly.
    config = _config(
        tmp_path, "c.yaml",
        "retention:\n  enabled: true\n  max_age_days: 1\n"
        "forensic:\n  retention_days: 3650\n",
    )
    now = 1_000_000.0
    day = 86400.0
    snap_dir = tmp_path / "snaps"

    pinned_crop = snap_dir / "pinned.jpg"
    make_file(pinned_crop, 10, now - 5 * day)
    pinned = db.add_tracklet(1, 1, now - 5 * day, now - 5 * day)
    db.update_tracklet_attributes(
        pinned, height_band=None, build=None, upper_color=None, lower_color=None,
        clothing_type=None, accessories=[], attr_conf=None,
        snapshot_path=str(pinned_crop), last_ts=now - 5 * day, now=now - 5 * day,
    )
    db.set_tracklet_pinned(pinned, 1)

    orphan = snap_dir / "orphan.jpg"
    make_file(orphan, 10, now - 5 * day)

    summary = RetentionPolicy(config, db, snap_dir).run_once(now)

    assert pinned_crop.exists()
    assert not orphan.exists()
    assert summary.snapshots_deleted == 1
