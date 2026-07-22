from pathlib import Path
from typing import Iterator

import numpy as np
import pytest

from camera.frame_buffer import Frame
from core.config import load_config
from forensic.attributes import AttributeSet
from forensic.index import MetadataIndex
from forensic.tracklet import CropJob
from forensic.worker import CropQueue, ForensicFacade, ForensicWorker
from plugins.base import Detection
from storage.database import Database
from storage.snapshots import SnapshotService


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "w.db")
    yield d
    d.close()


class _StubEmbedder:
    def embed(self, crops: list[np.ndarray]) -> np.ndarray:
        return np.tile(np.array([1.0, 0.0], np.float32), (len(crops), 1))


def _attrs() -> AttributeSet:
    return AttributeSet("red", "mavi", "orta", "orta")


def _config(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text("forensic:\n  enabled: true\n  queue_size: 3\n  batch_size: 8\n",
                 encoding="utf-8")
    return load_config(p)


def test_cropqueue_drops_oldest() -> None:
    q = CropQueue(2)
    for i in range(4):
        q.put(CropJob(i, np.zeros((4, 4, 3), np.uint8), float(i), _attrs()))
    drained = q.drain(10)
    assert [j.tracklet_id for j in drained] == [2, 3]
    assert q.dropped == 2


def test_process_batch_writes_attrs_and_embedding(db: Database, tmp_path: Path) -> None:
    idx = MetadataIndex(db)
    snaps = SnapshotService(tmp_path / "snaps")
    worker = ForensicWorker(CropQueue(8), idx, snaps, embedder=_StubEmbedder())
    tid = idx.ensure_tracklet(1, 7, 1.0, 1.0)
    worker.process_batch([CropJob(tid, np.full((20, 10, 3), 128, np.uint8), 2.0, _attrs())])
    t = db.get_tracklet(tid)
    assert t.upper_color == "red" and t.obs_count == 1
    assert t.snapshot_path is not None and Path(t.snapshot_path).exists()
    assert db.get_embedding(tid) is not None


def test_facade_offer_enqueues_and_returns_views(db: Database, tmp_path: Path) -> None:
    idx = MetadataIndex(db)
    snaps = SnapshotService(tmp_path / "snaps")
    facade = ForensicFacade(_config(tmp_path), idx, snaps, embedder=_StubEmbedder())
    img = np.zeros((200, 300, 3), np.uint8)
    img[:] = (0, 0, 255)
    frame = Frame(image=img, timestamp=0.0, seq=0)
    person = Detection("person", 0.9, (10, 10, 60, 160), "person", track_id=7)
    views = facade.offer(1, frame, [person], [])
    assert len(views) == 1 and views[0].track_id == 7


def test_facade_disabled_returns_empty(db: Database, tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("forensic:\n  enabled: false\n", encoding="utf-8")
    facade = ForensicFacade(load_config(p), MetadataIndex(db),
                            SnapshotService(tmp_path / "s"))
    frame = Frame(image=np.zeros((10, 10, 3), np.uint8), timestamp=0.0, seq=0)
    assert facade.offer(1, frame, [], []) == []


class _ShortClassifyEmbedder:
    def classify(self, crops: list[np.ndarray]) -> list[str]:
        # Return fewer items than batch size (e.g., 1 item for 2-job batch)
        return ["ceket"]

    def embed(self, crops: list[np.ndarray]) -> np.ndarray:
        return np.tile(np.array([1.0, 0.0], np.float32), (len(crops), 1))


def test_process_batch_short_classify_result(db: Database, tmp_path: Path) -> None:
    idx = MetadataIndex(db)
    snaps = SnapshotService(tmp_path / "snaps")
    worker = ForensicWorker(CropQueue(8), idx, snaps, embedder=_ShortClassifyEmbedder(),
                            attribute_model=_ShortClassifyEmbedder())
    # Create 2 tracklets
    tid1 = idx.ensure_tracklet(1, 7, 1.0, 1.0)
    tid2 = idx.ensure_tracklet(1, 8, 1.0, 1.0)
    # Process batch of 2, but classify returns only 1 item
    worker.process_batch([
        CropJob(tid1, np.full((20, 10, 3), 128, np.uint8), 2.0, _attrs()),
        CropJob(tid2, np.full((20, 10, 3), 128, np.uint8), 3.0, _attrs()),
    ])
    # Both tracklets should have attribute rows (no exception raised)
    t1 = db.get_tracklet(tid1)
    t2 = db.get_tracklet(tid2)
    assert t1.obs_count == 1 and t1.snapshot_path is not None
    assert t2.obs_count == 1 and t2.snapshot_path is not None
    # Both should have embeddings
    assert db.get_embedding(tid1) is not None
    assert db.get_embedding(tid2) is not None
