"""Camera ego-motion estimation: tell a moving camera (dashcam / vehicle-mounted) from a
fixed one, and recover the global image shift it induces.

A dashcam drags the WHOLE scene across the frame, so a parked car looks like it's moving and
a car keeping pace looks stopped. To read a vehicle's real speed we first estimate the
camera's own motion and subtract it (SpeedEstimator does the subtraction; this class supplies
the shift).

Method: sparse Lucas-Kanade optical flow on a fixed grid of points between consecutive
downscaled grayscale frames. The MEDIAN flow vector is the global/background motion (robust to
the handful of grid points that land on independently-moving objects), and the median flow
MAGNITUDE tells whether the camera itself is moving — on a fixed camera the background grid
points sit still (median ~0) even when objects cross the scene, whereas a moving camera pushes
almost every grid point. Panning/translating motion is compensated well; pure forward driving
(radial expansion, little net translation) is still flagged as moving but only partially
compensated — the honest limit of a monocular, depth-free estimate.

Worker-thread only. ~1-2 ms per frame on the downscaled image.
"""
from __future__ import annotations

import cv2
import numpy as np

_LK = dict(winSize=(21, 21), maxLevel=2,
           criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))


class EgoMotion:
    def __init__(self, width: int = 320, moving_flow: float = 1.3, ema: float = 0.4,
                 grid: tuple[int, int] = (16, 12)) -> None:
        self._w = int(width)
        self._moving = float(moving_flow)   # median grid-flow (downscaled px) => camera moving
        self._ema = min(1.0, max(0.05, float(ema)))
        self._gx, self._gy = int(grid[0]), int(grid[1])
        self._prev: np.ndarray | None = None
        self._scale = 1.0
        self._mag_ema = 0.0

    def reset(self) -> None:
        self._prev = None
        self._mag_ema = 0.0

    def _gray(self, frame: np.ndarray) -> np.ndarray:
        h, w = frame.shape[:2]
        self._scale = w / float(self._w) if w > self._w else 1.0
        sw = min(w, self._w)
        sh = max(1, int(round(h / self._scale)))
        small = cv2.resize(frame, (sw, sh)) if (sw, sh) != (w, h) else frame
        if small.ndim == 3:
            return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return small

    def _grid_points(self, w: int, h: int) -> np.ndarray:
        xs = np.linspace(w * 0.05, w * 0.95, self._gx)
        ys = np.linspace(h * 0.05, h * 0.95, self._gy)
        return np.array([[x, y] for y in ys for x in xs],
                        dtype=np.float32).reshape(-1, 1, 2)

    def update(self, frame: np.ndarray) -> tuple[float, float, bool]:
        """Return (dx, dy, moving): the global image shift in FULL-frame pixels since the last
        frame, and whether the camera is currently moving. A fixed camera returns ~(0, 0, False)."""
        g = self._gray(frame)
        prev = self._prev
        self._prev = g
        if prev is None or prev.shape != g.shape:
            return (0.0, 0.0, self._mag_ema > self._moving)
        h, w = g.shape
        pts = self._grid_points(w, h)
        try:
            nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, g, pts, None, **_LK)
        except cv2.error:
            return (0.0, 0.0, self._mag_ema > self._moving)
        if nxt is None or st is None:
            return (0.0, 0.0, self._mag_ema > self._moving)
        good = st.reshape(-1) == 1
        if int(good.sum()) < 6:
            return (0.0, 0.0, self._mag_ema > self._moving)
        flow = (nxt - pts).reshape(-1, 2)[good]
        mdx = float(np.median(flow[:, 0]))
        mdy = float(np.median(flow[:, 1]))
        mag = float(np.median(np.hypot(flow[:, 0], flow[:, 1])))
        self._mag_ema = self._ema * mag + (1 - self._ema) * self._mag_ema
        moving = self._mag_ema > self._moving
        if not moving:
            return (0.0, 0.0, False)        # fixed camera: don't inject flow noise into speeds
        return (mdx * self._scale, mdy * self._scale, True)
