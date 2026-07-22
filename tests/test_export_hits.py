import json
from pathlib import Path

from export.csv_json import hits_to_csv, hits_to_json
from forensic.search import SearchHit


def _hits() -> list[SearchHit]:
    return [
        SearchHit("tracklet", 100.0, 1, "TRACKLET", "red · tall", "a.jpg", None, 5),
        SearchHit("event", 90.0, 1, "VEHICLE", "araba", "b.jpg", (1, 2, 3, 4), 9),
    ]


def test_hits_to_csv(tmp_path: Path) -> None:
    path = tmp_path / "h.csv"
    hits_to_csv(_hits(), path)
    text = path.read_text(encoding="utf-8")
    assert "kind" in text and "tracklet" in text and "VEHICLE" in text


def test_hits_to_json(tmp_path: Path) -> None:
    path = tmp_path / "h.json"
    hits_to_json(_hits(), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data) == 2
    assert data[0]["type"] == "TRACKLET"
    assert data[1]["bbox"] == [1, 2, 3, 4]
