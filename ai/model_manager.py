"""Model download, cache, integrity verification and device selection."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import requests
from loguru import logger

_RELEASE_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/{name}"
_ATTEMPTS = 3


class ModelDownloadError(Exception):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class ModelManager:
    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def ensure_model(self, model_name: str, url: str | None = None,
                     sha256: str | None = None) -> Path:
        path = self._cache_dir / model_name
        sha_path = self._cache_dir / f"{model_name}.sha256"
        if path.exists():
            actual = _sha256(path)
            if sha256 is not None:
                if actual == sha256:
                    sha_path.write_text(actual, encoding="utf-8")
                    return path
            elif sha_path.exists() and actual == sha_path.read_text(
                    encoding="utf-8").strip():
                return path
            logger.warning("model {} failed integrity check, re-downloading", model_name)
            path.unlink(missing_ok=True)
            sha_path.unlink(missing_ok=True)
        self._download(model_name, path, url)
        actual = _sha256(path)
        if sha256 is not None and actual != sha256:
            path.unlink(missing_ok=True)
            raise ModelDownloadError(
                f"{model_name} sha256 mismatch: {actual} != {sha256}")
        sha_path.write_text(actual, encoding="utf-8")
        return path

    def _download(self, model_name: str, path: Path, url: str | None = None) -> None:
        target = url if url is not None else _RELEASE_URL.format(name=model_name)
        last_error: Exception | None = None
        for attempt in range(1, _ATTEMPTS + 1):
            try:
                logger.info("downloading {} (attempt {}/{})", target, attempt, _ATTEMPTS)
                with requests.get(target, stream=True, timeout=(10, 60)) as response:
                    response.raise_for_status()
                    tmp = path.with_suffix(".part")
                    with tmp.open("wb") as f:
                        for chunk in response.iter_content(chunk_size=1 << 20):
                            f.write(chunk)
                    tmp.replace(path)
                return
            except requests.RequestException as exc:
                last_error = exc
                time.sleep(2 ** attempt)
        raise ModelDownloadError(f"failed to download {model_name}: {last_error}")

    @staticmethod
    def select_device() -> str:
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda:0"
        except Exception:
            logger.exception("CUDA probe failed, falling back to CPU")
        return "cpu"
