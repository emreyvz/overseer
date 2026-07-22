"""Zone geometry primitives and the ZoneView overlay/panel model."""
from __future__ import annotations

from dataclasses import dataclass

ZONE_TYPES: list[str] = [
    "entrance", "exit", "parking", "cashier", "lobby", "restricted", "line",
    "queue", "custom",
]


@dataclass(frozen=True)
class ZoneView:
    zone_id: int
    name: str
    type: str
    polygon: list[tuple[int, int]]
    occupancy: int
    entries: int
    exits: int
    allowed_direction: str | None = None


def point_in_polygon(point: tuple[int, int], polygon: list[tuple[int, int]]) -> bool:
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and \
                (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _orient(a: tuple[int, int], b: tuple[int, int], c: tuple[int, int]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segment_intersects(p1: tuple[int, int], p2: tuple[int, int],
                       a: tuple[int, int], b: tuple[int, int]) -> bool:
    """True iff segments p1-p2 and a-b properly cross.

    Detects proper transversal crossings only (not collinear overlap or an
    endpoint merely touching the other segment). Order-independent in both
    (p1,p2) and (a,b).
    """
    d1 = _orient(a, b, p1)
    d2 = _orient(a, b, p2)
    d3 = _orient(p1, p2, a)
    d4 = _orient(p1, p2, b)
    return d1 * d2 < 0 and d3 * d4 < 0


def side_sign(a: tuple[int, int], b: tuple[int, int], p: tuple[int, int]) -> int:
    o = _orient(a, b, p)
    return (o > 0) - (o < 0)
