# tests/test_tracklets_db.py
from pathlib import Path
from typing import Iterator

import numpy as np
import pytest

from storage.database import Database, Tracklet


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def _add(db: Database, source_id: int = 1, track_id: int = 7,
         first_ts: float = 100.0) -> int:
    return db.add_tracklet(source_id, track_id, first_ts, first_ts)


def test_add_and_update_tracklet(db: Database) -> None:
    tid = _add(db)
    db.update_tracklet_attributes(
        tid, height_band="uzun", build="orta", upper_color="red",
        lower_color="mavi", clothing_type=None, accessories=["backpack"],
        attr_conf=0.8, snapshot_path="data/snapshots/t.jpg", last_ts=150.0, now=150.0,
    )
    t = db.get_tracklet(tid)
    assert isinstance(t, Tracklet)
    assert t.upper_color == "red" and t.lower_color == "mavi"
    assert t.accessories == ["backpack"]
    assert t.obs_count == 1  # update increments
    assert t.last_ts == 150.0


def test_embedding_roundtrip(db: Database) -> None:
    tid = _add(db)
    vec = np.array([0.6, 0.8], dtype=np.float32)  # L2 norm = 1
    db.set_tracklet_embedding(tid, vec.tobytes(), dim=2, model_id="osnet", now=1.0)
    raw, dim = db.get_embedding(tid)
    back = np.frombuffer(raw, dtype=np.float32)
    assert dim == 2
    assert np.allclose(back, vec)


def test_candidate_embeddings_filter(db: Database) -> None:
    a = _add(db, track_id=1)
    b = _add(db, track_id=2)
    c = _add(db, track_id=3)
    for tid, color in ((a, "red"), (b, "red"), (c, "mavi")):
        db.update_tracklet_attributes(
            tid, height_band="orta", build="orta", upper_color=color,
            lower_color="siyah", clothing_type=None, accessories=[], attr_conf=1.0,
            snapshot_path=None, last_ts=1.0, now=1.0)
        db.set_tracklet_embedding(tid, np.ones(2, np.float32).tobytes(), 2, "osnet", 1.0)
    cands = db.candidate_embeddings(a, upper_color="red")
    ids = {cid for cid, _, _ in cands}
    assert ids == {b}  # excludes self (a), excludes mavi (c)


def test_prune_respects_pinned(db: Database) -> None:
    old_a = db.add_tracklet(1, 1, 10.0, 10.0)
    old_b = db.add_tracklet(1, 2, 10.0, 10.0)
    db.set_tracklet_embedding(old_a, np.ones(2, np.float32).tobytes(), 2, "osnet", 10.0)
    db.set_tracklet_pinned(old_b, 1)
    deleted = db.prune_tracklets_older_than(100.0)
    assert deleted == 1
    assert db.get_tracklet(old_a) is None          # pruned
    assert db.get_embedding(old_a) is None          # cascade
    assert db.get_tracklet(old_b) is not None       # pinned survives
    assert db.count_tracklets() == 1
