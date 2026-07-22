"""CSV/JSON exporters for events and daily statistics."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path

from storage.database import DailyStat, StoredEvent

_EVENT_FIELDS = ["id", "timestamp", "type", "source_id", "label", "confidence",
                 "bbox", "snapshot_path"]
_STAT_FIELDS = ["day_start", "source_id", "event_type", "count", "updated_at"]
_HIT_FIELDS = ["kind", "ts", "source_id", "type", "label", "snapshot_path",
               "bbox", "ref_id"]


def events_to_csv(events: list[StoredEvent], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_EVENT_FIELDS)
        writer.writeheader()
        for event in events:
            writer.writerow({
                "id": event.id, "timestamp": event.timestamp, "type": event.type,
                "source_id": event.source_id, "label": event.label,
                "confidence": event.confidence,
                "bbox": json.dumps(list(event.bbox)) if event.bbox else "",
                "snapshot_path": event.snapshot_path or "",
            })


def events_to_json(events: list[StoredEvent], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = []
    for event in events:
        item = asdict(event)
        item["bbox"] = list(event.bbox) if event.bbox else None
        data.append(item)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def stats_to_csv(stats: list[DailyStat], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_STAT_FIELDS)
        writer.writeheader()
        for stat in stats:
            writer.writerow(asdict(stat))


def stats_to_json(stats: list[DailyStat], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [asdict(stat) for stat in stats]
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def hits_to_csv(hits: list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_HIT_FIELDS)
        writer.writeheader()
        for hit in hits:
            writer.writerow({
                "kind": hit.kind, "ts": hit.ts, "source_id": hit.source_id,
                "type": hit.type, "label": hit.label,
                "snapshot_path": hit.snapshot_path or "",
                "bbox": json.dumps(list(hit.bbox)) if hit.bbox else "",
                "ref_id": hit.ref_id,
            })


def hits_to_json(hits: list, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = []
    for hit in hits:
        data.append({
            "kind": hit.kind, "ts": hit.ts, "source_id": hit.source_id,
            "type": hit.type, "label": hit.label, "snapshot_path": hit.snapshot_path,
            "bbox": list(hit.bbox) if hit.bbox else None, "ref_id": hit.ref_id,
        })
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
