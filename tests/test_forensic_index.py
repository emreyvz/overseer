from pathlib import Path
from typing import Iterator

import numpy as np
import pytest

from forensic.attributes import AttributeSet
from forensic.index import BruteForceIndex, MetadataIndex
from storage.database import Database


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "i.db")
    yield d
    d.close()


def test_bruteforce_ranks_by_cosine() -> None:
    q = np.array([1.0, 0.0], dtype=np.float32)
    cands = [
        (1, np.array([1.0, 0.0], dtype=np.float32)),   # identical
        (2, np.array([0.0, 1.0], dtype=np.float32)),   # orthogonal
        (3, np.array([0.7071, 0.7071], dtype=np.float32)),
    ]
    ranked = BruteForceIndex().find_similar(q, cands, k=2)
    assert [cid for cid, _ in ranked] == [1, 3]
    assert ranked[0][1] == pytest.approx(1.0, abs=1e-3)


def _attrs(color: str) -> AttributeSet:
    return AttributeSet(upper_color=color, lower_color="black",
                        height_band="medium", build="medium")


def test_metadata_index_find_similar(db: Database) -> None:
    idx = MetadataIndex(db)
    now = 100.0
    ids = {}
    for track_id, (name, color, vec) in enumerate((
        ("q", "red", [1.0, 0.0]),
        ("near", "red", [0.99, 0.14]),
        ("far", "red", [0.0, 1.0]),
        ("other", "blue", [1.0, 0.0]),
    ), start=1):
        tid = idx.ensure_tracklet(1, track_id, now, now)
        idx.save_sample(tid, _attrs(color), snapshot_path=None, now=now)
        idx.set_embedding(tid, np.array(vec, dtype=np.float32), now)
        ids[name] = tid
    # Filter to red: excludes "other" even though its vector is identical to q.
    ranked = idx.find_similar(ids["q"], k=5, filters={"upper_color": "red"})
    ranked_ids = [cid for cid, _ in ranked]
    assert ids["near"] in ranked_ids
    assert ids["other"] not in ranked_ids
    assert ranked_ids[0] == ids["near"]  # closest red


def test_find_similar_no_embedding(db: Database) -> None:
    idx = MetadataIndex(db)
    tid = idx.ensure_tracklet(1, 5, 1.0, 1.0)
    assert idx.find_similar(tid) == []
