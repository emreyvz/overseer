"""Relationship discovery: a living co-occurrence graph between roster entities.

Every time the background harvester sees several subjects in the same frame on the same
camera, those subjects are co-present — recorded here as pairwise edges that accumulate
count, camera coverage and a time span over the session. From that raw co-occurrence the
graph derives associates ("frequently seen together"), each with a confidence score, without
any manually defined rule. Session-scoped and in-memory, mirroring SessionRoster.

Thread-safe: the harvester thread writes, API threads read.
"""
from __future__ import annotations

import threading


def _key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


class RelationshipGraph:
    def __init__(self, saturate: int = 20) -> None:
        self._sat = max(1, int(saturate))          # co-sightings at which count-confidence maxes
        self._edges: dict[tuple[str, str], dict] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._edges.clear()

    def observe_together(self, ids: list[str], cam: str | None, now: float) -> None:
        """Record that these subjects were co-present (same camera, same moment)."""
        uniq = sorted({i for i in ids if i})
        if len(uniq) < 2:
            return
        with self._lock:
            for i in range(len(uniq)):
                for j in range(i + 1, len(uniq)):
                    k = (uniq[i], uniq[j])
                    e = self._edges.get(k)
                    if e is None:
                        self._edges[k] = {"count": 1, "first": now, "last": now,
                                          "cams": {cam} if cam else set()}
                    else:
                        e["count"] += 1
                        e["last"] = now
                        if cam:
                            e["cams"].add(cam)

    def _confidence(self, e: dict) -> float:
        cams = len(e["cams"])
        by_count = min(1.0, e["count"] / self._sat)
        by_cams = min(cams, 3) / 3.0
        return round(min(1.0, by_count * 0.7 + by_cams * 0.3), 2)

    def _entry(self, other: str, e: dict) -> dict:
        return {"id": other, "count": e["count"], "cameras": sorted(c for c in e["cams"] if c),
                "first": e["first"] * 1000, "last": e["last"] * 1000,
                "confidence": self._confidence(e)}

    def for_entity(self, eid: str, limit: int = 12, min_count: int = 1) -> list[dict]:
        """Subjects most associated with `eid`, strongest first."""
        out: list[dict] = []
        with self._lock:
            for (a, b), e in self._edges.items():
                if e["count"] < min_count:
                    continue
                if a == eid or b == eid:
                    out.append(self._entry(b if a == eid else a, e))
        out.sort(key=lambda x: (-x["confidence"], -x["count"]))
        return out[:limit]

    def graph(self, min_count: int = 2, limit: int = 300) -> dict:
        """Nodes and weighted edges for the social graph, strongest edges first."""
        with self._lock:
            edges = [{"a": a, "b": b, "count": e["count"], "confidence": self._confidence(e),
                      "cameras": sorted(c for c in e["cams"] if c)}
                     for (a, b), e in self._edges.items() if e["count"] >= min_count]
        edges.sort(key=lambda x: -x["confidence"])
        edges = edges[:limit]
        nodes = sorted({x["a"] for x in edges} | {x["b"] for x in edges})
        return {"nodes": nodes, "edges": edges}
