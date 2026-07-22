from pathlib import Path

import pytest

from ai.model_manager import ModelDownloadError, ModelManager

CACHE = Path("models")  # project root; in .gitignore — cache persists across tests


def test_ensure_model_downloads_and_caches() -> None:
    mgr = ModelManager(CACHE)
    path = mgr.ensure_model("yolo11n.pt")
    assert path.exists()
    assert path.stat().st_size > 1_000_000
    assert (CACHE / "yolo11n.pt.sha256").exists()
    # Second call does not download, returns the same path
    assert mgr.ensure_model("yolo11n.pt") == path


def test_corrupted_model_redownloaded() -> None:
    mgr = ModelManager(CACHE)
    path = mgr.ensure_model("yolo11n.pt")
    original_size = path.stat().st_size
    path.write_bytes(b"corrupted")
    path2 = mgr.ensure_model("yolo11n.pt")
    assert path2.stat().st_size == original_size


def test_unknown_model_raises(tmp_path: Path) -> None:
    mgr = ModelManager(tmp_path)
    with pytest.raises(ModelDownloadError):
        mgr.ensure_model("no-such-model-xyz.pt")


def test_select_device_matches_torch() -> None:
    import torch

    device = ModelManager.select_device()
    if torch.cuda.is_available():
        assert device == "cuda:0"
    else:
        assert device == "cpu"
