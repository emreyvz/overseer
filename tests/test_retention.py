from pathlib import Path
from typing import Iterator

import pytest

from core.config import Config, load_config
from events.types import Event, EventType
from storage.database import Database
from storage.retention import RetentionPolicy


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "retention:\n  enabled: true\n  max_age_days: 10\n  max_total_gb: 0.000001\n",
        encoding="utf-8",
    )
    return load_config(p)


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "r.db")
    yield d
    d.close()


def make_file(path: Path, size: int, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    import os
    os.utime(path, (mtime, mtime))


def test_age_based_pruning(config: Config, db: Database, tmp_path: Path) -> None:
    now = 1_000_000.0
    day = 86400.0
    snap_dir = tmp_path / "snaps"
    old_clip = tmp_path / "rec" / "old.mp4"
    new_clip = tmp_path / "rec" / "new.mp4"
    make_file(old_clip, 100, now - 20 * day)
    make_file(new_clip, 100, now - 1 * day)
    db.add_recording("clip", str(old_clip), now - 20 * day, now - 20 * day, "event",
                     None, 1, 100)
    db.add_recording("clip", str(new_clip), now - 1 * day, now - 1 * day, "event",
                     None, 1, 100)
    old_snap = snap_dir / "old.jpg"
    new_snap = snap_dir / "new.jpg"
    make_file(old_snap, 10, now - 30 * day)
    make_file(new_snap, 10, now - 1 * day)
    db.add_event(Event(type=EventType.MOTION, timestamp=now - 30 * day,
                       source_id=1, label="motion"))
    db.add_event(Event(type=EventType.MOTION, timestamp=now - 1 * day,
                       source_id=1, label="motion"))

    policy = RetentionPolicy(config, db, snap_dir)
    summary = policy.run_once(now)

    assert not old_clip.exists() and new_clip.exists()
    assert not old_snap.exists() and new_snap.exists()
    assert summary.recordings_deleted == 1
    assert summary.snapshots_deleted == 1
    assert summary.events_deleted == 1
    assert len(db.list_recordings()) == 1
    assert db.count_events_since(0) == 1


def test_size_based_pruning(db: Database, tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    # 10-day age (nothing old), tiny size cap forces size-based deletion.
    p.write_text(
        "retention:\n  enabled: true\n  max_age_days: 3650\n  max_total_gb: 0.0000002\n",
        encoding="utf-8",
    )
    config = load_config(p)
    now = 1_000_000.0
    for i in range(3):
        f = tmp_path / "rec" / f"c{i}.mp4"
        make_file(f, 100, now - (3 - i))  # c0 oldest
        db.add_recording("clip", str(f), now - (3 - i), now, "event", None, 1, 100)
    policy = RetentionPolicy(config, db, tmp_path / "snaps")
    summary = policy.run_once(now)
    # 300 bytes total; cap ~200 bytes -> delete oldest until <= cap
    assert summary.recordings_deleted >= 1
    assert db.total_recordings_size() <= 200


def test_disabled_noop(db: Database, tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("retention:\n  enabled: false\n", encoding="utf-8")
    config = load_config(p)
    db.add_recording("clip", "x.mp4", 1.0, 2.0, "event", None, 1, 100)
    summary = RetentionPolicy(config, db, tmp_path).run_once(1_000_000.0)
    assert summary.recordings_deleted == 0
    assert len(db.list_recordings()) == 1
