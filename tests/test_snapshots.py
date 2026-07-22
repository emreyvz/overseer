from pathlib import Path

import cv2
import numpy as np

from storage.snapshots import SnapshotService


def test_save_creates_dated_unique_files(tmp_path: Path) -> None:
    svc = SnapshotService(tmp_path)
    img = np.full((32, 32, 3), 128, dtype=np.uint8)
    p1 = svc.save(img, prefix="person")
    p2 = svc.save(img, prefix="person")
    assert p1.exists() and p2.exists()
    assert p1 != p2
    assert p1.parent.name == p1.parent.name  # dated folder
    assert p1.name.startswith("person_")
    loaded = cv2.imread(str(p1))
    assert loaded is not None
    assert loaded.shape == (32, 32, 3)
