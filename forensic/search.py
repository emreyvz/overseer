"""Unified forensic search: parse text + filters -> merged tracklet/event hits."""
from __future__ import annotations

from dataclasses import dataclass, field

from forensic.query import parse_query
from storage.database import Database, StoredEvent, Tracklet


@dataclass(frozen=True)
class SearchHit:
    kind: str            # "tracklet" | "event"
    ts: float
    source_id: int | None
    type: str            # EventType.name or "TRACKLET"
    label: str
    snapshot_path: str | None
    bbox: tuple | None
    ref_id: int


@dataclass(frozen=True)
class SearchResult:
    hits: list[SearchHit] = field(default_factory=list)
    deferred_terms: list[str] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)


def summarize_tracklet(t: Tracklet) -> str:
    colors = "/".join(c for c in (t.upper_color, t.lower_color) if c)
    parts = [p for p in (colors, t.height_band, t.build) if p]
    if t.accessories:
        parts.append(", ".join(t.accessories))
    return " · ".join(parts) if parts else "tracklet"


def _tracklet_hit(t: Tracklet) -> SearchHit:
    return SearchHit(kind="tracklet", ts=t.last_ts, source_id=t.source_id,
                     type="TRACKLET", label=summarize_tracklet(t),
                     snapshot_path=t.snapshot_path, bbox=None, ref_id=t.id)


def _event_hit(e: StoredEvent) -> SearchHit:
    return SearchHit(kind="event", ts=e.timestamp, source_id=e.source_id,
                     type=e.type, label=e.label, snapshot_path=e.snapshot_path,
                     bbox=e.bbox, ref_id=e.id)


class ForensicSearchService:
    def __init__(self, db: Database) -> None:
        self._db = db

    def search(self, text: str | None = None,
               filters: dict | None = None) -> SearchResult:
        filters = dict(filters or {})
        q = parse_query(text) if text else None

        def merged(name: str) -> list[str]:
            out: list[str] = list(filters.get(name) or [])
            if q is not None:
                for v in getattr(q, name):
                    if v not in out:
                        out.append(v)
            return out

        colors = merged("colors")
        clothing_types = merged("clothing_types")
        accessories = merged("accessories")
        height_bands = merged("height_bands")
        builds = merged("builds")
        event_types = merged("event_types")
        source_id = filters.get("source_id")
        start = filters.get("start")
        end = filters.get("end")
        limit = int(filters.get("limit", 500))
        deferred = list(q.deferred_terms) if q is not None else []
        unmatched = list(q.unmatched) if q is not None else []

        hits: list[SearchHit] = []
        if colors or clothing_types or accessories or height_bands or builds:
            for t in self._db.search_tracklets(
                colors=colors or None, clothing_types=clothing_types or None,
                accessories=accessories or None, height_bands=height_bands or None,
                builds=builds or None, source_id=source_id, start=start, end=end,
                limit=limit,
            ):
                hits.append(_tracklet_hit(t))
        if event_types:
            for e in self._db.search_events(
                start if start is not None else 0.0,
                end if end is not None else 1e12,
                types=event_types, source_id=source_id, limit=limit,
            ):
                hits.append(_event_hit(e))
        hits.sort(key=lambda h: h.ts, reverse=True)
        return SearchResult(hits=hits[:limit], deferred_terms=deferred,
                            unmatched=unmatched)
