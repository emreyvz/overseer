"""Per-track speed estimation for vehicles (and anything else worth clocking).

Tracks each object's ground-contact point (bottom centre of the box) over a short time
window and turns pixel displacement into a smoothed km/h estimate. Absolute km/h needs a
scene scale (metres represented by one pixel); without a real per-camera calibration we use
a configurable rough default, so the number is honestly an ESTIMATE — the UI marks it as
such. What IS reliable is the RELATIVE reading: a fast car reads faster than a slow one on
the same camera, and a parked car reads ~0.

Worker-thread only. O(1) per update; bounded memory per track.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field


@dataclass
class _Track:
    points: deque = field(default_factory=deque)   # (x, y, ex, ey, t): point + cumulative ego
    ema: float | None = None                        # smoothed km/h
    last_seen: float = 0.0
    ego: tuple[float, float] = (0.0, 0.0)           # running camera shift at this track's path
    mpp_ema: float | None = None                    # smoothed metres-per-pixel from object size


def _bottom_center(bbox: tuple[float, float, float, float]) -> tuple[float, float]:
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, y2)


class SpeedEstimator:
    """Smoothed km/h per track id from bounding-box motion.

    The pixel→metre scale is the crux of a believable speed. A single fixed
    meters_per_pixel cannot work: perspective makes the SAME real speed cover very different
    pixel distances near vs far, so a fixed scale reads a near car far too fast and a distant
    one far too slow. So the scale is derived PER OBJECT from its apparent size: given the
    object's real-world height (metres, by class) and its bounding-box height in pixels,
    metres-per-pixel = real_height / box_height at that object's depth. That auto-calibrates
    with distance and keeps speeds roughly metric and consistent across the frame, no manual
    per-camera calibration needed. meters_per_pixel is only the fallback when no size ref is
    supplied.

    window: seconds averaged over. ema_alpha: smoothing of the newest reading. min_kmh:
    readings below this report as 0 (stationary) so idle jitter isn't a crawling "2 km/h".
    """

    def __init__(self, meters_per_pixel: float = 0.05, window: float = 1.2,
                 ema_alpha: float = 0.4, min_kmh: float = 3.0,
                 max_kmh: float = 300.0, buffer: int = 40, expire: float = 4.0,
                 scale_alpha: float = 0.3) -> None:
        self._mpp = max(1e-6, float(meters_per_pixel))
        self._window = max(0.1, float(window))
        self._alpha = min(1.0, max(0.05, float(ema_alpha)))
        self._min_kmh = max(0.0, float(min_kmh))
        self._max_kmh = float(max_kmh)
        self._buffer = int(buffer)
        self._expire = float(expire)
        self._scale_alpha = min(1.0, max(0.05, float(scale_alpha)))
        self._tracks: dict[int, _Track] = {}

    def reset(self) -> None:
        self._tracks.clear()

    def update(self, track_id: int, bbox: tuple[float, float, float, float],
               now: float, ego_delta: tuple[float, float] = (0.0, 0.0),
               scale_ref_m: float | None = None, scale_reliable: bool = True) -> float | None:
        """Fold in one sighting; return the current smoothed km/h estimate (>= 0), or None
        until there is enough motion history to say anything.

        ego_delta is the camera's PER-FRAME image shift at this object's location (from
        EgoMotion.flow_at), accumulated internally and subtracted from the object's displacement
        so the speed is measured relative to the ground, not the frame — on a moving camera a
        parked car reads ~0 and a car keeping pace reads the camera's speed. Pass (0, 0) for a
        fixed camera.

        scale_ref_m is the object's real-world height in metres (by class). With it, the pixel
        scale is derived from the object's own box height (metres-per-pixel = scale_ref_m /
        box_height_px) so the speed is depth-correct and roughly metric. scale_reliable=False
        (e.g. the box is clipped by a frame edge, so its height is wrong) keeps the last good
        scale instead of recomputing from a bad height."""
        tr = self._tracks.get(track_id)
        if tr is None:
            tr = _Track(points=deque(maxlen=self._buffer))
            self._tracks[track_id] = tr
        x, y = _bottom_center(bbox)
        tr.ego = (tr.ego[0] + ego_delta[0], tr.ego[1] + ego_delta[1])
        ex, ey = tr.ego
        tr.points.append((x, y, ex, ey, now))
        tr.last_seen = now
        # per-object pixel scale from apparent size (auto-calibrates for perspective/depth)
        box_h = abs(bbox[3] - bbox[1])
        if scale_ref_m and scale_reliable and box_h > 1.0:
            mpp_now = float(scale_ref_m) / box_h
            tr.mpp_ema = (mpp_now if tr.mpp_ema is None
                          else self._scale_alpha * mpp_now + (1 - self._scale_alpha) * tr.mpp_ema)
        mpp = tr.mpp_ema if tr.mpp_ema is not None else self._mpp
        # drop anything older than the averaging window (keep one straddling sample)
        while len(tr.points) > 2 and now - tr.points[1][4] > self._window:
            tr.points.popleft()
        if len(tr.points) < 2:
            return None
        x0, y0, ex0, ey0, t0 = tr.points[0]
        dt = now - t0
        if dt <= 1e-3:
            return None
        # ground-relative displacement = object displacement minus the camera's own shift
        gdx = (x - x0) - (ex - ex0)
        gdy = (y - y0) - (ey - ey0)
        px_per_s = math.hypot(gdx, gdy) / dt
        kmh = px_per_s * mpp * 3.6
        if kmh > self._max_kmh:            # implausible (track-id reuse / teleport) -> ignore
            return tr.ema
        if kmh < self._min_kmh:
            kmh = 0.0
        tr.ema = kmh if tr.ema is None else self._alpha * kmh + (1 - self._alpha) * tr.ema
        return tr.ema

    def get(self, track_id: int) -> float | None:
        tr = self._tracks.get(track_id)
        return tr.ema if tr else None

    def prune(self, now: float) -> None:
        stale = [tid for tid, tr in self._tracks.items()
                 if now - tr.last_seen >= self._expire]
        for tid in stale:
            del self._tracks[tid]
