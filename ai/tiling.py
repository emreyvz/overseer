"""Sliced-inference helpers (SAHI-style) for small/distant object recall.

A distant person is only a handful of pixels once the whole frame is squeezed down to the
detector's input size, so it is missed. Running the detector on overlapping tiles instead
lets each tile be up-scaled, so the same person is large enough to detect at the SAME
confidence — recovering recall without the false positives you get from just lowering the
threshold globally. These helpers are pure (tile geometry + overlap de-duplication); the
model inference lives in the YOLO backend.
"""
from __future__ import annotations

Box = tuple[int, int, int, int]  # x1, y1, x2, y2


def tiles(h: int, w: int, n: int, overlap: float = 0.2) -> list[Box]:
    """An n×n grid of tile rectangles covering (h, w), each grown by ``overlap`` of a cell
    on every side so objects on a seam still land whole inside some tile. n<=1 -> whole frame."""
    if n <= 1 or h <= 0 or w <= 0:
        return [(0, 0, w, h)]
    step_x, step_y = w / n, h / n
    ox, oy = step_x * overlap, step_y * overlap
    out: list[Box] = []
    for r in range(n):
        for c in range(n):
            x0 = max(0, int(c * step_x - ox))
            y0 = max(0, int(r * step_y - oy))
            x1 = min(w, int((c + 1) * step_x + ox))
            y1 = min(h, int((r + 1) * step_y + oy))
            if x1 > x0 and y1 > y0:
                out.append((x0, y0, x1, y1))
    return out


def iou(a: Box, b: Box) -> float:
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def select_supplements(existing: list[Box], candidates: list[tuple[Box, float]],
                       iou_thresh: float = 0.45) -> list[int]:
    """Pick the tile detections worth ADDING: those that don't overlap a box already found
    on the full frame, nor a higher-confidence tile detection (so an object caught in two
    overlapping tiles is added once). Returns indices into ``candidates``, best-conf first."""
    order = sorted(range(len(candidates)), key=lambda i: -candidates[i][1])
    kept_boxes = list(existing)
    keep: list[int] = []
    for i in order:
        box = candidates[i][0]
        if any(iou(box, e) >= iou_thresh for e in kept_boxes):
            continue
        keep.append(i)
        kept_boxes.append(box)
    return keep
