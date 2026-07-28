"""Camera ego-motion estimation: tell a moving camera (dashcam / vehicle-mounted) from a
fixed one, and model the global image flow it induces so vehicle speeds can be measured
relative to the ground rather than the frame.

A dashcam drags the WHOLE scene across the frame, so a parked car looks like it's moving and
a car keeping pace looks stopped. A pure translation (pan) model can't fix the second case:
driving straight forward produces RADIAL flow (expansion), whose median vector is ~0 even
though the scene is clearly moving. So instead of a single translation we fit an AFFINE flow
field to a grid of optical-flow vectors — affine's scale term captures the forward-motion
expansion, and evaluating it at each object's own location gives the LOCAL background motion
there. Subtracting that (SpeedEstimator does it) makes an off-centre car keeping pace read the
camera's speed, and a parked car read ~0.

Method: sparse Lucas-Kanade optical flow on a fixed grid between consecutive downscaled gray
frames; a robust (2-pass, outlier-trimmed) least-squares affine fit to the flow vectors. The
median flow magnitude decides whether the camera itself is moving. Fixed camera => model is
identity (zero flow) and `flow_at` returns (0, 0).

Limitation: an object sitting exactly on the focus-of-expansion (dead ahead) induces no ego
flow, so a monocular estimate can't recover its speed there — an honest depth-free limit.

Worker-thread only. ~1-2 ms per frame on the downscaled image.
"""
from __future__ import annotations

import cv2
import numpy as np

_LK = dict(winSize=(21, 21), maxLevel=2,
           criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 20, 0.03))


class EgoMotion:
    def __init__(self, width: int = 320, moving_flow: float = 1.3, ema: float = 0.4,
                 grid: tuple[int, int] = (24, 16)) -> None:
        self._w = int(width)
        self._moving = float(moving_flow)   # median grid-flow (downscaled px) => camera moving
        self._ema = min(1.0, max(0.05, float(ema)))
        self._gx, self._gy = int(grid[0]), int(grid[1])
        self._prev: np.ndarray | None = None
        self._scale = 1.0
        self._mag_ema = 0.0
        self._cx: np.ndarray | None = None   # affine coeffs for flow-x: [a, b, c] over (x,y,1)
        self._cy: np.ndarray | None = None   # affine coeffs for flow-y
        self._moving_now = False

    def reset(self) -> None:
        self._prev = None
        self._mag_ema = 0.0
        self._cx = self._cy = None
        self._moving_now = False

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

    @staticmethod
    def _fit_affine(pts: np.ndarray, flow: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Robust least-squares affine fit: flow ≈ M·[x, y, 1]. Two 2-pass fits (x and y), the
        second dropping the high-residual points so a few independently-moving objects among
        the mostly-background grid don't skew the camera model."""
        a = np.column_stack([pts[:, 0], pts[:, 1], np.ones(len(pts))]).astype(np.float64)
        idx = np.arange(len(pts))
        cx = cy = None
        for _ in range(2):
            cx, *_ = np.linalg.lstsq(a[idx], flow[idx, 0], rcond=None)
            cy, *_ = np.linalg.lstsq(a[idx], flow[idx, 1], rcond=None)
            res = np.hypot(a @ cx - flow[:, 0], a @ cy - flow[:, 1])
            keep = res <= (np.median(res) * 2.0 + 1e-3)
            if int(keep.sum()) >= 6:
                idx = np.where(keep)[0]
        return cx, cy

    def update(self, frame: np.ndarray) -> bool:
        """Ingest a frame; return whether the camera is currently moving. Builds the affine ego
        model that `flow_at` samples. A fixed camera yields a zero model."""
        g = self._gray(frame)
        prev = self._prev
        self._prev = g
        if prev is None or prev.shape != g.shape:
            self._cx = self._cy = None
            return self._moving_now
        h, w = g.shape
        pts = self._grid_points(w, h)
        try:
            nxt, st, _ = cv2.calcOpticalFlowPyrLK(prev, g, pts, None, **_LK)
        except cv2.error:
            self._cx = self._cy = None
            return self._moving_now
        if nxt is None or st is None:
            self._cx = self._cy = None
            return self._moving_now
        good = st.reshape(-1) == 1
        if int(good.sum()) < 8:
            self._cx = self._cy = None
            return self._moving_now
        p0 = pts.reshape(-1, 2)[good]
        flow = (nxt.reshape(-1, 2) - pts.reshape(-1, 2))[good]
        mag = float(np.median(np.hypot(flow[:, 0], flow[:, 1])))
        self._mag_ema = self._ema * mag + (1 - self._ema) * self._mag_ema
        self._moving_now = self._mag_ema > self._moving
        if not self._moving_now:
            self._cx = self._cy = None      # fixed camera: no flow to inject into speeds
        else:
            self._cx, self._cy = self._fit_affine(p0, flow)
        return self._moving_now

    def moving(self) -> bool:
        return self._moving_now

    def flow_at(self, x: float, y: float) -> tuple[float, float]:
        """The modelled camera-induced flow at full-frame point (x, y), in FULL-frame px per
        frame. (0, 0) when the camera is fixed or no model is available."""
        if not self._moving_now or self._cx is None or self._cy is None:
            return (0.0, 0.0)
        xs, ys = x / self._scale, y / self._scale
        fx = float(self._cx[0] * xs + self._cx[1] * ys + self._cx[2])
        fy = float(self._cy[0] * xs + self._cy[1] * ys + self._cy[2])
        return (fx * self._scale, fy * self._scale)
