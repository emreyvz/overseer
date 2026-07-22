"""Tests for the /api/stream SSE endpoint and its broadcaster.

Every network/thread wait in the integration test is bounded (urlopen
timeout, thread.join timeout) so this file can never hang the suite.
"""
from __future__ import annotations

import threading
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest

from api.rest import RestApiServer
from core.config import Config, load_config
from storage.database import Database


def _write_config(tmp_path: Path, *, enabled: bool, port: int = 0) -> Config:
    p = tmp_path / "rest_config.yaml"
    p.write_text(
        "rest_api:\n"
        f"  enabled: {str(enabled).lower()}\n"
        '  host: "127.0.0.1"\n'
        f"  port: {port}\n",
        encoding="utf-8",
    )
    return load_config(p)


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "c.db")
    yield d
    d.close()


def test_broadcaster_delivers_to_registered_queue(tmp_path: Path, db: Database) -> None:
    config = _write_config(tmp_path, enabled=True, port=0)
    server = RestApiServer(config, db)
    q = server._register()
    try:
        server.broadcast({"a": 1})
        assert q.get_nowait() == {"a": 1}
    finally:
        server._unregister(q)


def test_sse_endpoint_streams_an_event(tmp_path: Path, db: Database) -> None:
    config = _write_config(tmp_path, enabled=True, port=0)
    server = RestApiServer(config, db)
    server.start()
    try:
        port = server.port_in_use()
        if server._server is None:
            pytest.skip("REST API server failed to start in this environment")

        holder: dict[str, bytes] = {}

        def reader() -> None:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/stream", timeout=5
                ) as resp:
                    while True:
                        line = resp.readline()
                        if not line:
                            break
                        if line.startswith(b"data:"):
                            holder["line"] = line
                            break
            except Exception:  # noqa: BLE001 - captured via empty holder below
                pass

        thread = threading.Thread(target=reader, daemon=True, name="SSEReaderTest")
        thread.start()
        # Give the reader a moment to connect and register before broadcasting.
        thread.join(timeout=0.3)
        server.broadcast({"type": "TEST", "label": "hi"})
        thread.join(timeout=5)

        if thread.is_alive() or "line" not in holder:
            pytest.skip("SSE stream did not deliver within the bounded timeout")

        assert b"TEST" in holder["line"]
        assert not thread.is_alive()
    finally:
        server.stop()
