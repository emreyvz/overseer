from pathlib import Path
from typing import Iterator

import numpy as np
import pytest

from camera.frame_buffer import Frame
from core.config import load_config
from forensic.index import MetadataIndex
from forensic.worker import ForensicFacade
from plugins.base import Detection
from storage.database import Database
from storage.snapshots import SnapshotService


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "e.db")
    yield d
    d.close()


class _StubEmbedder:
    def embed(self, crops):
        return np.tile(np.array([0.6, 0.8], np.float32), (len(crops), 1))


def _config(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "forensic:\n  enabled: true\n  sample_interval_seconds: 0.0\n"
        "  queue_size: 32\n  batch_size: 8\n",
        encoding="utf-8",
    )
    return load_config(p)


def test_end_to_end_persist_and_search(db: Database, tmp_path: Path) -> None:
    idx = MetadataIndex(db)
    facade = ForensicFacade(_config(tmp_path), idx,
                            SnapshotService(tmp_path / "snaps"),
                            embedder=_StubEmbedder())
    img = np.zeros((200, 300, 3), np.uint8)
    img[:100] = (0, 0, 255)     # upper red
    img[100:] = (255, 0, 0)     # alt blue
    frame = Frame(image=img, timestamp=0.0, seq=0)
    person = Detection("person", 0.9, (20, 10, 80, 170), "person", track_id=7)
    accessory = Detection("backpack", 0.8, (30, 20, 60, 80), "accessory")

    views = facade.offer(1, frame, [person], [accessory])
    assert views[0].attributes.upper_color == "red"

    # Process synchronously (no thread flakiness): drain the facade's queue.
    facade._worker.process_batch(facade._queue.drain(10))

    tid = views[0].tracklet_id
    t = db.get_tracklet(tid)
    assert t.upper_color == "red" and t.lower_color == "blue"
    assert t.accessories == ["backpack"]
    assert db.get_embedding(tid) is not None
    facade.stop()
