"""Intent estimation: detection says WHAT a subject is doing; this estimates WHY, as
probabilities rather than facts.

From a track's recent motion it derives a few honest features — how much it dwells in place,
how straight its path is (net displacement over path length), how often it reverses, and how
fast it moves — and maps them to candidate intents (loitering, waiting, searching, pacing,
hurrying, transiting) each with a confidence and a one-line explanation. Never asserts: the
UI shows the leading guess and its probability, and low-confidence reads say nothing.

Worker-thread only. O(window) per update; bounded memory per track.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field


@dataclass
class _Track:
    points: deque = field(default_factory=deque)   # (x, y, t) ground-contact samples
    last_seen: float = 0.0


def _bottom_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, y2)


class IntentEstimator:
    def __init__(self, window: float = 6.0, min_span: float = 2.0, min_points: int = 6,
                 still_frac: float = 0.06, loiter_seconds: float = 6.0,
                 buffer: int = 90, expire: float = 4.0) -> None:
        self._window = float(window)
        self._min_span = float(min_span)
        self._min_points = int(min_points)
        self._still_frac = float(still_frac)   # step < still_frac * frame_diag => "not moving"
        self._loiter = float(loiter_seconds)
        self._buffer = int(buffer)
        self._expire = float(expire)
        self._tracks: dict[int, _Track] = {}

    def reset(self) -> None:
        self._tracks.clear()

    def prune(self, now: float) -> None:
        for tid in [t for t, tr in self._tracks.items() if now - tr.last_seen >= self._expire]:
            del self._tracks[tid]

    def update(self, track_id: int, bbox: tuple[float, float, float, float], now: float,
               frame_diag: float = 1000.0) -> dict | None:
        """Return the leading intent as {intent, confidence, why, alt?} or None when there
        isn't enough motion yet / nothing stands out."""
        tr = self._tracks.get(track_id)
        if tr is None:
            tr = _Track(points=deque(maxlen=self._buffer))
            self._tracks[track_id] = tr
        x, y = _bottom_center(bbox)
        bx1, by1, bx2, by2 = bbox
        aspect = (bx2 - bx1) / max(1.0, (by2 - by1))   # width/height: >1 horizontal, <0.6 upright
        tr.points.append((x, y, now, aspect))
        tr.last_seen = now
        while len(tr.points) > 2 and now - tr.points[1][2] > self._window:
            tr.points.popleft()
        if len(tr.points) < self._min_points:
            return None
        x0, y0, t0 = tr.points[0]
        span = now - t0
        if span < self._min_span:
            return None

        pts = list(tr.points)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        spread = math.hypot(max(xs) - min(xs), max(ys) - min(ys))   # how far it ranges overall
        loiter_r = 0.10 * frame_diag        # stays inside this radius => dwelling in place
        move_eps = 0.012 * frame_diag       # a real step (per frame), for path + reversal counts
        total = 0.0
        reversals = 0
        prev_dir = 0.0
        for i in range(1, len(pts)):
            dx = pts[i][0] - pts[i - 1][0]
            dy = pts[i][1] - pts[i - 1][1]
            total += math.hypot(dx, dy)
            if abs(dx) > move_eps and abs(dx) > abs(dy):    # horizontal flip => pacing
                d = 1.0 if dx > 0 else -1.0
                if prev_dir and d != prev_dir:
                    reversals += 1
                prev_dir = d
        net = math.dist((x0, y0), (x, y))
        path_ratio = net / total if total > 1e-6 else 0.0    # 1 = straight, 0 = wandering
        speed_px = total / span                              # px per second
        dwell = max(0.0, 1.0 - spread / loiter_r)            # 1 = tight, 0 at/over the radius
        asps = sorted(p[3] for p in pts)
        aspect = asps[len(asps) // 2]                        # median box shape over the window
        horizontal = aspect > 1.15                           # wider than tall => lying / swimming
        seated = 0.62 < aspect <= 1.15                       # compact box => seated posture

        scores: list[tuple[str, float, str]] = []
        if dwell > 0.35:                                     # staying in one place: read the posture
            s = round(dwell * min(1.0, span / self._loiter), 2)
            if horizontal:
                scores.append(("lying down", max(s, 0.4), f"horizontal, still for {int(span)}s"))
            elif seated:
                scores.append(("sitting", max(s, 0.4), f"seated, still for {int(span)}s"))
            else:
                label = "loitering" if span >= self._loiter else "waiting"
                scores.append((label, s, f"standing still for {int(span)}s"))
        if spread >= loiter_r * 0.8:                         # genuinely moving around
            if horizontal and speed_px < 0.16 * frame_diag:  # horizontal + slow drift => swimming
                scores.append(("swimming", round(min(0.85, 0.4 + path_ratio * 0.5), 2),
                               "horizontal, moving slowly through the scene"))
            elif path_ratio > 0.65:
                if speed_px > 0.30 * frame_diag:
                    label = "running"
                elif speed_px > 0.18 * frame_diag:
                    label = "hurrying"
                elif speed_px < 0.055 * frame_diag:
                    label = "strolling"
                else:
                    label = "transiting"
                scores.append((label, round(min(1.0, path_ratio), 2),
                               f"direct movement ({int(path_ratio * 100)}% direct)"))
            elif total > move_eps * 4 and path_ratio < 0.45:
                s = min(1.0, (0.45 - path_ratio) / 0.45 + 0.3)
                label = "pacing" if reversals >= 2 else ("wandering" if speed_px < 0.08 * frame_diag else "searching")
                scores.append((label, round(s, 2),
                               f"moving but not getting anywhere ({int(path_ratio * 100)}% direct)"))
            if reversals >= 3:
                scores.append(("monitoring surroundings", round(min(1.0, reversals / 5.0), 2),
                               f"{reversals} direction changes"))
        if not scores:
            return None
        scores.sort(key=lambda z: -z[1])
        top = scores[0]
        if top[1] < 0.3:
            return None
        out = {"intent": top[0], "confidence": top[1], "why": top[2]}
        if len(scores) > 1 and scores[1][1] >= 0.3:
            out["alt"] = scores[1][0]
        return out
