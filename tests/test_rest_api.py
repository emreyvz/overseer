from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest

from alerts.model import Alert
from api.rest import RestApiServer
from core.config import Config, load_config
from events.types import Event, EventType
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


def _get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode())


def test_rest_api_serves_events_alerts_stats_health(tmp_path: Path, db: Database) -> None:
    db.add_event(Event(type=EventType.PERSON, timestamp=1000.0, source_id=1,
                        label="person", confidence=0.9))
    db.add_event(Event(type=EventType.VEHICLE, timestamp=1001.0, source_id=1,
                        label="vehicle", confidence=0.8))
    db.add_alert(Alert(rule_id=1, rule_name="Test Rule", event_type="PERSON",
                       source_id=1, severity="warning", summary="test alert",
                       timestamp=1000.0))

    config = _write_config(tmp_path, enabled=True, port=0)
    server = RestApiServer(config, db)
    server.start()
    try:
        port = server.port_in_use()
        base = f"http://127.0.0.1:{port}"

        events = _get_json(f"{base}/api/events")
        assert isinstance(events, list)
        assert len(events) == 2
        labels = {e["label"] for e in events}
        assert labels == {"person", "vehicle"}

        health = _get_json(f"{base}/api/health")
        assert health["status"] == "ok"
        assert "time" in health

        alerts = _get_json(f"{base}/api/alerts")
        assert isinstance(alerts, list)
        assert len(alerts) == 1
        assert alerts[0]["summary"] == "test alert"

        stats = _get_json(f"{base}/api/stats?start=0&end=999999999")
        assert isinstance(stats, dict)
        assert "type_counts" in stats
        assert stats["type_counts"].get("PERSON") == 1
        assert stats["type_counts"].get("VEHICLE") == 1

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(f"{base}/api/nope", timeout=5)
        assert exc_info.value.code == 404

        req = urllib.request.Request(f"{base}/api/events", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 405
    finally:
        server.stop()


def test_rest_api_disabled_by_default_binds_nothing(tmp_path: Path, db: Database) -> None:
    config = _write_config(tmp_path, enabled=False)
    server = RestApiServer(config, db)
    server.start()
    assert server._server is None
    server.stop()  # safe no-op


def test_metrics_prometheus_endpoint(tmp_path: Path, db: Database) -> None:
    import time

    db.add_event(Event(type=EventType.MOTION, timestamp=time.time(), source_id=1,
                       label="motion"))
    db.add_event(Event(type=EventType.MOTION, timestamp=time.time(), source_id=1,
                       label="motion"))
    db.add_event(Event(type=EventType.PERSON, timestamp=time.time(), source_id=1,
                       label="person"))
    config = _write_config(tmp_path, enabled=True, port=0)
    server = RestApiServer(config, db)
    server.start()
    try:
        port = server.port_in_use()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as r:
            body = r.read().decode()
        assert "# TYPE overseer_events_total gauge" in body
        assert "overseer_events_total 3" in body
        assert 'overseer_event_type_total{type="MOTION"} 2' in body
        assert "overseer_alerts_unacked 0" in body
    finally:
        server.stop()
