"""YAML-backed configuration with dotted-key access and mtime-based reload."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml


class Config:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._mtime = 0.0
        self.data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        with self._path.open("r", encoding="utf-8") as f:
            self.data = yaml.safe_load(f) or {}
        self._mtime = self._path.stat().st_mtime

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self.data
            for part in key.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            parts = key.split(".")
            node = self.data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value

    def reload(self) -> bool:
        with self._lock:
            mtime = self._path.stat().st_mtime
            if mtime == self._mtime:
                return False
            self._load()
            return True


def load_config(path: Path) -> Config:
    return Config(path)
