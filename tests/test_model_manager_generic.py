# tests/test_model_manager_generic.py
import hashlib
from pathlib import Path

import pytest

from ai.model_manager import ModelDownloadError, ModelManager


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_ensure_with_url_and_sha_offline(tmp_path: Path, monkeypatch) -> None:
    mgr = ModelManager(tmp_path)
    payload = b"hello-model"

    def fake_download(model_name: str, path: Path, url: str | None = None) -> None:
        assert url == "http://example/m.bin"
        path.write_bytes(payload)

    monkeypatch.setattr(mgr, "_download", fake_download)
    path = mgr.ensure_model("m.bin", url="http://example/m.bin", sha256=_sha(payload))
    assert path.read_bytes() == payload
    assert (tmp_path / "m.bin.sha256").read_text().strip() == _sha(payload)
    # Second call: cached, no download needed (fake would still succeed but sha matches).
    assert mgr.ensure_model("m.bin", url="http://example/m.bin",
                            sha256=_sha(payload)) == path


def test_ensure_sha_mismatch_raises(tmp_path: Path, monkeypatch) -> None:
    mgr = ModelManager(tmp_path)
    monkeypatch.setattr(mgr, "_download",
                        lambda name, path, url=None: path.write_bytes(b"wrong"))
    with pytest.raises(ModelDownloadError):
        mgr.ensure_model("m.bin", url="http://example/m.bin", sha256=_sha(b"expected"))
