import csv
import json
from pathlib import Path

from export.csv_json import (
    events_to_csv, events_to_json, stats_to_csv, stats_to_json,
)
from storage.database import DailyStat, StoredEvent


def events() -> list[StoredEvent]:
    return [
        StoredEvent(id=1, timestamp=100.0, type="PERSON", source_id=1, label="person",
                    confidence=0.9, bbox=(1, 2, 3, 4), snapshot_path="a.jpg",
                    metadata={"track_id": 7}),
        StoredEvent(id=2, timestamp=200.0, type="MOTION", source_id=None,
                    label="motion", confidence=None, bbox=None, snapshot_path=None,
                    metadata={}),
    ]


def stats() -> list[DailyStat]:
    return [DailyStat(day_start=86400.0, source_id=1, event_type="PERSON",
                      count=5, updated_at=1.0)]


def test_events_to_csv(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "e.csv"
    events_to_csv(events(), path)
    assert path.exists()
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert rows[0]["type"] == "PERSON"
    assert rows[0]["label"] == "person"


def test_events_to_json(tmp_path: Path) -> None:
    path = tmp_path / "e.json"
    events_to_json(events(), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == 2
    assert data[0]["bbox"] == [1, 2, 3, 4]
    assert data[1]["bbox"] is None


def test_stats_to_csv(tmp_path: Path) -> None:
    path = tmp_path / "s.csv"
    stats_to_csv(stats(), path)
    with path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["count"] == "5"
    assert rows[0]["event_type"] == "PERSON"


def test_stats_to_json(tmp_path: Path) -> None:
    path = tmp_path / "s.json"
    stats_to_json(stats(), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data[0]["count"] == 5
