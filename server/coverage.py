"""FOG OF WAR — the observability field, and its complement.

Every other part of Overseer answers "what is there". This answers "what could be there that we
would never know about". Four independent channels combine into one `unseen` scalar per ground
cell:

  geometric   ray-occlusion shadow volumes cast by standing objects
  optical     pixels-per-metre against the DORI thresholds of EN 62676-4
  radiometric local SNR, clipping and motion blur
  empirical   where tracks actually die — the only channel that measures real failure

The geometry is deliberately NOT built on the monocular depth field. Relative depth has no
metric scale, so ranges derived from it would be a guess dressed up as a measurement. Instead
everything comes from a pinhole ground-plane model (camera height + tilt + FOV), which is the
same model the tactical map already uses, and which degrades honestly: the depth field is used
only to find WHICH pixels are standing objects, never HOW FAR they are.

Pure functions first (no I/O, no model, no cv2 state) so the geometry is unit-testable in
isolation; `CoverageField` on top holds the accumulation that needs a database.

A note on scale: camera height is a configured estimate unless the operator calibrates it, so
every metre value this module produces is approximate and is flagged `scale_estimated` all the
way out to the UI. The RELATIVE ordering (near vs far, this spot vs that spot) is reliable even
when the absolute number is not, and that is what the product actually needs.
"""
from __future__ import annotations

import json
import math
import time
from typing import Any

import cv2
import numpy as np

# EN 62676-4 (DORI) — pixels per metre on target required for each task.
DORI: dict[str, float] = {
    "detect": 25.0,
    "observe": 63.0,
    "recognise": 125.0,
    "identify": 250.0,
}
DORI_ORDER = ("detect", "observe", "recognise", "identify")

_EPS = 1e-6


# ── pinhole ground-plane model ──────────────────────────────────────────────────────────────

class GroundModel:
    """Maps between image rows and ground ranges for a camera looking at a flat plane.

    `cam_height_m` is the height of the optical centre above the ground, `pitch` the downward
    tilt of the optical axis in radians. Both are estimates; see the module docstring.
    """

    __slots__ = ("fov_h", "fov_v", "pitch", "cam_h", "w", "h", "tan_v", "tan_h")

    def __init__(self, fov_h_deg: float, width: int, height: int, cam_height_m: float,
                 pitch_rad: float) -> None:
        self.w = max(1, int(width))
        self.h = max(1, int(height))
        self.fov_h = math.radians(max(1.0, min(179.0, float(fov_h_deg))))
        self.tan_h = math.tan(self.fov_h / 2.0)
        # vertical FOV follows from the aspect ratio under a square-pixel pinhole
        self.fov_v = 2.0 * math.atan(self.tan_h * self.h / self.w)
        self.tan_v = math.tan(self.fov_v / 2.0)
        self.cam_h = max(0.2, float(cam_height_m))
        self.pitch = float(pitch_rad)

    # -- rows <-> ranges ---------------------------------------------------------------------
    def alpha(self, ny: float) -> float:
        """Downward angle of the ray through normalized image row `ny` (0 top, 1 bottom)."""
        return self.pitch + math.atan((ny - 0.5) * 2.0 * self.tan_v)

    def range_at(self, ny: float) -> float | None:
        """Ground range in metres for image row `ny`, or None above the horizon."""
        a = self.alpha(ny)
        if a <= 1e-4:
            return None                      # at or above the horizon: the ray never lands
        return self.cam_h / math.tan(a)

    def row_at(self, z: float) -> float:
        """Normalized image row where ground range `z` appears. Clamped to the frame."""
        a = math.atan(self.cam_h / max(_EPS, float(z)))
        ny = 0.5 + math.tan(a - self.pitch) / (2.0 * self.tan_v + _EPS)
        return float(min(1.5, max(-0.5, ny)))

    @property
    def horizon_ny(self) -> float:
        """Normalized row of the horizon (may fall outside [0,1] for a steep camera)."""
        return float(0.5 - math.tan(self.pitch) / (2.0 * self.tan_v + _EPS))

    # -- optics ------------------------------------------------------------------------------
    def px_per_m(self, z: float) -> float:
        """Pixels per metre on a target at range `z` — the DORI quantity."""
        return self.w / (2.0 * max(_EPS, float(z)) * self.tan_h)

    def range_for_px_per_m(self, ppm: float) -> float:
        return self.w / (2.0 * max(_EPS, float(ppm)) * self.tan_h)

    def dori_class(self, z: float) -> str:
        ppm = self.px_per_m(z)
        best = "blind"
        for task in DORI_ORDER:
            if ppm >= DORI[task]:
                best = task
        return best

    # -- ground area of one image cell -------------------------------------------------------
    def cell_area_m2(self, nx0: float, nx1: float, ny0: float, ny1: float) -> float:
        """Ground area subtended by an image cell. Near cells cover a sliver of ground and far
        cells cover a huge wedge, so coverage MUST be area-weighted or it silently reports the
        near field as if it were the whole scene."""
        z_far = self.range_at(ny0)      # smaller ny = higher in frame = farther
        z_near = self.range_at(ny1)
        if z_far is None or z_near is None or z_far <= z_near:
            return 0.0
        # trapezoid between two ranges, width given by the angular extent at each range
        b0 = math.atan((nx0 - 0.5) * 2.0 * self.tan_h)
        b1 = math.atan((nx1 - 0.5) * 2.0 * self.tan_h)
        dbeta = abs(b1 - b0)
        w_near, w_far = 2.0 * z_near * math.tan(dbeta / 2.0), 2.0 * z_far * math.tan(dbeta / 2.0)
        return float(0.5 * (w_near + w_far) * (z_far - z_near))


def estimate_pitch(disp01: np.ndarray | None, fov_h_deg: float, width: int, height: int,
                   default_pitch_deg: float = 12.0) -> float:
    """Estimate the camera's downward tilt from where the depth field flattens out.

    The horizon is the row above which disparity stops changing (everything is 'far'). Finding
    it gives the tilt for free, which is far better than asking an installer for a number they
    will not measure. Falls back to a modest default when there is no usable depth.
    """
    fallback = math.radians(default_pitch_deg)
    if disp01 is None or disp01.ndim != 2 or disp01.shape[0] < 8:
        return fallback
    rows = disp01.mean(axis=1)                          # mean disparity per row, far -> near
    lo, hi = float(rows.min()), float(rows.max())
    if hi - lo < 0.05:
        return fallback                                  # flat scene (indoors, a wall): no horizon
    # first row (from the top) whose mean disparity has risen 12% of the way to the maximum
    thresh = lo + 0.12 * (hi - lo)
    idx = int(np.argmax(rows > thresh))
    if idx <= 0 or idx >= len(rows) - 1:
        return fallback
    ny_h = idx / float(len(rows))
    if not (0.02 < ny_h < 0.92):
        return fallback
    tan_v = math.tan(math.atan(math.tan(math.radians(fov_h_deg) / 2.0) * height / width))
    return float(math.atan((0.5 - ny_h) * 2.0 * tan_v))


# ── channel 1: geometric occlusion ──────────────────────────────────────────────────────────

def shadow_from_blob(gm: GroundModel, nx0: float, nx1: float, ny_top: float, ny_base: float,
                     target_h: float) -> dict | None:
    """The ground a standing object hides from view, for a target of height `target_h`.

    Given the camera at height hc and an occluder of height ph standing at range Zp, a target of
    height th at range Z is hidden when the ray to its top passes below the occluder's top:

        Z < Zp * (hc - th) / (hc - ph)

    which reduces to the familiar hc/(hc-ph) for a target on the ground, goes to infinity once
    the occluder is as tall as the camera, and correctly yields NO shadow for a target taller
    than the occluder. That single expression is the whole channel.
    """
    zp = gm.range_at(ny_base)
    if zp is None or zp <= 0.3:
        return None
    a_top = gm.alpha(ny_top)
    ph = gm.cam_h - zp * math.tan(a_top)                 # occluder height from its top row
    if ph <= 0.15:
        return None                                       # too short to hide anything meaningful
    if target_h >= ph:
        return None                                       # the target simply sees over it
    denom = gm.cam_h - ph
    if denom <= 0.05:
        z_far = None                                      # taller than the camera: shadow to horizon
    else:
        z_far = zp * (gm.cam_h - target_h) / denom
    ny_far = gm.horizon_ny if z_far is None else gm.row_at(z_far)
    ny_far = max(ny_far, gm.horizon_ny)                   # never extend past the horizon
    if ny_base - ny_far < 0.004:
        return None                                       # degenerate sliver
    return {
        "polygon": [[nx0, ny_far], [nx1, ny_far], [nx1, ny_base], [nx0, ny_base]],
        "occluder": [nx0, ny_top, nx1 - nx0, ny_base - ny_top],
        "z_near": round(float(zp), 2),
        "z_far": None if z_far is None else round(float(z_far), 2),
        "height_m": round(float(ph), 2),
    }


def blobs_from_mask(mask: np.ndarray, min_area_frac: float = 0.0012,
                    max_blobs: int = 24) -> list[tuple[float, float, float, float]]:
    """Normalized (nx0, ny_top, nx1, ny_base) boxes of the standing objects in a foreground mask."""
    if mask is None or mask.size == 0:
        return []
    h, w = mask.shape[:2]
    m = (mask > 0).astype(np.uint8)
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(m, connectivity=8)
    out: list[tuple[float, float, float, float, int]] = []
    min_area = max(4.0, min_area_frac * h * w)
    for i in range(1, n):
        x, y, bw, bh, area = stats[i]
        if area < min_area or bh < 3:
            continue
        out.append((x / w, y / h, (x + bw) / w, (y + bh) / h, int(area)))
    out.sort(key=lambda t: -t[4])
    return [(a, b, c, d) for (a, b, c, d, _e) in out[:max_blobs]]


# ── channel 3: radiometric quality ──────────────────────────────────────────────────────────

def radiometric_quality(bgr: np.ndarray, grid: tuple[int, int]) -> np.ndarray:
    """Per-cell photometric usability in [0,1]: texture against noise, minus clipping and blur.

    A dark corner, a blown highlight beside a lamp and a permanently smeared patch of lens dirt
    all land here, and the last of those is a free finding nobody was looking for.
    """
    gw, gh = grid
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    g = g.astype(np.float32)
    small = cv2.resize(g, (gw, gh), interpolation=cv2.INTER_AREA)
    # local contrast: std within each cell, via the box-filter identity E[x^2] - E[x]^2
    sq = cv2.resize(g * g, (gw, gh), interpolation=cv2.INTER_AREA)
    var = np.maximum(0.0, sq - small * small)
    contrast = np.sqrt(var) / 48.0                                  # ~1.0 at a healthy texture level
    # clipping: fraction of pixels pinned at either end of the range
    clip = cv2.resize(((g < 4) | (g > 251)).astype(np.float32), (gw, gh), interpolation=cv2.INTER_AREA)
    # blur: normalized Laplacian energy, low where edges are smeared
    lap = cv2.Laplacian(g, cv2.CV_32F, ksize=3)
    sharp = cv2.resize(np.abs(lap), (gw, gh), interpolation=cv2.INTER_AREA) / 12.0
    q = np.clip(0.55 * np.clip(contrast, 0, 1) + 0.45 * np.clip(sharp, 0, 1), 0.0, 1.0)
    q *= (1.0 - np.clip(clip * 1.5, 0.0, 1.0))
    return q.astype(np.float32)


def specular_mask(bgr: np.ndarray, grid: tuple[int, int]) -> np.ndarray:
    """Cells that are probably glass, water or a mirror, where depth is meaningless.

    Bright, low-saturation and temporally featureless. These are marked INDETERMINATE rather
    than UNSEEN: not knowing whether you know is a third state and it deserves its own answer.
    """
    gw, gh = grid
    if bgr.ndim != 3:
        return np.zeros((gh, gw), np.float32)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    bright = cv2.resize(hsv[:, :, 2].astype(np.float32), (gw, gh), interpolation=cv2.INTER_AREA)
    sat = cv2.resize(hsv[:, :, 1].astype(np.float32), (gw, gh), interpolation=cv2.INTER_AREA)
    return ((bright > 205) & (sat < 42)).astype(np.float32)


# ── channel 4: empirical track mortality ────────────────────────────────────────────────────

class TrackMortality:
    """Where tracks actually die.

    Counts, per ground cell, how many tracks entered it, how many drew their last breath there
    and how many sprang into existence there. Deaths and births away from the frame border are
    the operational definition of a blind spot, and this is the only channel that measures the
    system's real failures instead of predicting them. Kept in RAM and flushed in batches.
    """

    BORDER = 0.05          # ignore starts/ends within this fraction of the frame edge
    STALE_S = 3.0          # a track unseen for this long is considered ended

    def __init__(self, cells: int) -> None:
        self.cells = int(cells)
        self.enter: dict[int, int] = {}
        self.die: dict[int, int] = {}
        self.born: dict[int, int] = {}
        self._live: dict[str, tuple[int, float, bool]] = {}   # det_id -> (cell, ts, born_inside)
        self._dirty: set[int] = set()

    @staticmethod
    def _interior(nx: float, ny: float) -> bool:
        b = TrackMortality.BORDER
        return b < nx < 1.0 - b and b < ny < 1.0 - b

    def observe(self, det_id: str, nx: float, ny: float, cell: int, now: float) -> None:
        prev = self._live.get(det_id)
        if prev is None:
            born_inside = self._interior(nx, ny)
            if born_inside:
                self.born[cell] = self.born.get(cell, 0) + 1
                self._dirty.add(cell)
            self.enter[cell] = self.enter.get(cell, 0) + 1
            self._dirty.add(cell)
            self._live[det_id] = (cell, now, born_inside)
            return
        pcell, _pts, born_inside = prev
        if cell != pcell:
            self.enter[cell] = self.enter.get(cell, 0) + 1
            self._dirty.add(cell)
        self._live[det_id] = (cell, now, born_inside)

    def sweep(self, now: float, interior_ids: dict[str, bool] | None = None) -> None:
        """Retire tracks that have gone quiet, counting a death where they were last seen."""
        gone = [k for k, (_c, ts, _b) in self._live.items() if now - ts > self.STALE_S]
        for k in gone:
            cell, _ts, _b = self._live.pop(k)
            if interior_ids is None or interior_ids.get(k, True):
                self.die[cell] = self.die.get(cell, 0) + 1
                self._dirty.add(cell)

    def drain(self) -> list[tuple[int, int, int, int]]:
        """(cell, enter, die, born) rows for the dirty cells, then clear the dirty set."""
        rows = [(c, self.enter.get(c, 0), self.die.get(c, 0), self.born.get(c, 0))
                for c in sorted(self._dirty)]
        self._dirty.clear()
        return rows


def mortality_rate(n_enter: int, n_die: int, n_born: int, min_samples: int = 20
                   ) -> tuple[float, bool]:
    """Beta-posterior mortality with an explicit 'not enough evidence' flag.

    A cell crossed four times that lost one track is not a 25% death trap, it is unknown. The
    Beta(1,1) prior pulls low-sample cells toward the middle, and the caller is told outright
    that the number should not be trusted.
    """
    if n_enter < max(1, min_samples):
        return 0.0, False
    deaths = float(n_die + n_born)
    trials = float(2 * n_enter)
    return float((deaths + 1.0) / (trials + 2.0)), True


# ── the field ───────────────────────────────────────────────────────────────────────────────

class CoverageField:
    """Builds and persists the observability field for one camera."""

    def __init__(self, db: Any, config: Any) -> None:
        self.db = db
        self.config = config
        gw = int(config.get("coverage.grid_w", 48)) if hasattr(config, "get") else 48
        self.grid: tuple[int, int] = (gw, max(8, int(gw * 9 / 16)))
        self.cell_count = self.grid[0] * self.grid[1]
        self.mortality: dict[int, TrackMortality] = {}
        self._last_flush = 0.0
        self._shadows: dict[int, list[dict]] = {}          # source_id -> latest shadow set
        self._losses: dict[str, dict] = {}                 # det_id -> live LOST IN FOG record
        self._occ_hits: dict[int, dict[int, int]] = {}     # persistence accumulator per source

    # -- configuration helpers ---------------------------------------------------------------
    def _cfg(self, key: str, default: Any) -> Any:
        try:
            return self.config.get(f"coverage.{key}", default)
        except Exception:
            return default

    def cell_of(self, nx: float, ny: float) -> int:
        gw, gh = self.grid
        cx = min(gw - 1, max(0, int(nx * gw)))
        cy = min(gh - 1, max(0, int(ny * gh)))
        return cy * gw + cx

    def _mort(self, source_id: int) -> TrackMortality:
        m = self.mortality.get(source_id)
        if m is None:
            m = TrackMortality(self.cell_count)
            self.mortality[source_id] = m
            self._load_mortality(source_id, m)
        return m

    def _load_mortality(self, source_id: int, m: TrackMortality) -> None:
        try:
            rows = self.db.query(
                "SELECT cell, n_enter, n_die, n_born FROM coverage_cells WHERE source_id = ?",
                (int(source_id),))
        except Exception:
            return
        for cell, ne, nd, nb in rows:
            if ne:
                m.enter[int(cell)] = int(ne)
            if nd:
                m.die[int(cell)] = int(nd)
            if nb:
                m.born[int(cell)] = int(nb)

    # -- live accumulation (called from the analysis worker) ---------------------------------
    def observe(self, source_id: int | None, dets: list[dict], now: float) -> None:
        """Accumulate the empirical channel. Two dict updates per track: deliberately cheap."""
        if source_id is None or not self._cfg("enabled", True):
            return
        m = self._mort(int(source_id))
        for d in dets:
            bbox = d.get("bbox")
            if not bbox or d.get("cls") not in ("person", "vehicle"):
                continue
            nx = float(bbox[0]) + float(bbox[2]) / 2.0
            ny = min(0.999, float(bbox[1]) + float(bbox[3]))       # foot point
            m.observe(str(d.get("id")), nx, ny, self.cell_of(nx, ny), now)
        m.sweep(now)
        if now - self._last_flush > 30.0:
            self._last_flush = now
            self.flush(int(source_id))

    def flush(self, source_id: int) -> None:
        m = self.mortality.get(source_id)
        if m is None:
            return
        rows = m.drain()
        if not rows:
            return
        now = time.time()
        try:
            self.db.execute_many(
                "INSERT INTO coverage_cells (source_id, cell, n_enter, n_die, n_born, updated_at)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(source_id, cell) DO UPDATE SET"
                " n_enter=excluded.n_enter, n_die=excluded.n_die, n_born=excluded.n_born,"
                " updated_at=excluded.updated_at",
                [(int(source_id), c, e, d, b, now) for (c, e, d, b) in rows])
        except Exception:
            pass

    # -- LOST IN FOG -------------------------------------------------------------------------
    def check_losses(self, source_id: int | None, dets: list[dict], now: float) -> list[dict]:
        """Subjects that walked into a shadow and have not walked out of it in time.

        The tolerance is derived from the subject's own speed and the depth of the shadow they
        entered, so a fast walker crossing a narrow wedge is given a short leash and someone
        stepping behind a lorry is given a long one.
        """
        if source_id is None:
            return []
        shadows = self._shadows.get(int(source_id)) or []
        if not shadows:
            self._losses.clear()
            return []
        tol = float(self._cfg("lost_tolerance", 2.5))
        seen: set[str] = set()
        for d in dets:
            bbox = d.get("bbox")
            if not bbox or d.get("cls") != "person":
                continue
            did = str(d.get("id"))
            seen.add(did)
            if did in self._losses:
                self._losses.pop(did, None)                # reappeared: nothing to report
                continue
            nx = float(bbox[0]) + float(bbox[2]) / 2.0
            ny = min(0.999, float(bbox[1]) + float(bbox[3]))
            for sh in shadows:
                poly = sh.get("polygon") or []
                if len(poly) < 4:
                    continue
                x0, y0 = poly[0][0], poly[0][1]
                x1, y1 = poly[2][0], poly[2][1]
                if x0 <= nx <= x1 and y0 <= ny <= y1:
                    depth = max(0.02, abs(y1 - y0))
                    speed = max(0.01, float(d.get("_ny_speed", 0.05)))
                    self._losses[did] = {
                        "det_id": did, "spot": int(sh.get("id", 0)), "entered": now,
                        "expected_exit": now + (depth / speed) * tol, "overdue": False,
                    }
                    break
        out: list[dict] = []
        for did, rec in list(self._losses.items()):
            if did in seen:
                continue
            if now > rec["expected_exit"]:
                if not rec["overdue"]:
                    rec["overdue"] = True
                    out.append(dict(rec))
                if now - rec["entered"] > 120.0:
                    self._losses.pop(did, None)            # give up on very old records
        return out

    # -- the build ---------------------------------------------------------------------------
    def build(self, source_id: int, cam: str, bgr: np.ndarray, disp01: np.ndarray | None,
              fov_deg: float, *, task: str | None = None,
              target_h: float | None = None) -> dict:
        """Assemble the full coverage payload for one frame."""
        task = (task or str(self._cfg("dori_task", "recognise"))).lower()
        if task not in DORI:
            task = "recognise"
        target_h = float(target_h if target_h is not None else self._cfg("target_height_m", 1.7))
        cam_h = float(self._cfg("camera_height_m", 3.0))
        horizon_m = float(self._cfg("horizon_m", 60.0))
        gw, gh = self.grid
        h0, w0 = bgr.shape[:2]

        pitch = estimate_pitch(disp01, fov_deg, w0, h0,
                               float(self._cfg("default_pitch_deg", 12.0)))
        gm = GroundModel(fov_deg, w0, h0, cam_h, pitch)

        # channel 1 — shadows cast by standing objects
        shadows: list[dict] = []
        if disp01 is not None and float(self._cfg("channels.geometric", 1.0)) > 0:
            mask = _foreground(disp01)
            for i, (nx0, nyt, nx1, nyb) in enumerate(blobs_from_mask(mask)):
                sh = shadow_from_blob(gm, nx0, nx1, nyt, nyb, target_h)
                if sh is not None:
                    sh["id"] = i
                    sh["persistent"] = False
                    shadows.append(sh)
        self._shadows[int(source_id)] = shadows
        self._track_persistence(int(source_id), shadows)

        # channel 3 — radiometric quality, and the indeterminate (specular) cells
        quality = radiometric_quality(bgr, (gw, gh))
        indet = specular_mask(bgr, (gw, gh))

        # channel 4 — empirical mortality
        m = self._mort(int(source_id))
        min_samples = int(self._cfg("min_cell_samples", 20))

        wg = float(self._cfg("channels.geometric", 1.0))
        wo = float(self._cfg("channels.optical", 1.0))
        wr = float(self._cfg("channels.radiometric", 0.7))
        we = float(self._cfg("channels.empirical", 1.0))

        unseen = np.zeros(gw * gh, np.float32)
        areas = np.zeros(gw * gh, np.float32)
        need_ppm = DORI[task]
        for cy in range(gh):
            ny0, ny1 = cy / gh, (cy + 1) / gh
            nyc = (ny0 + ny1) / 2.0
            z = gm.range_at(nyc)
            for cx in range(gw):
                idx = cy * gw + cx
                nx0, nx1 = cx / gw, (cx + 1) / gw
                if z is None or z > horizon_m:
                    areas[idx] = 0.0                        # sky, or past the reporting horizon
                    unseen[idx] = 0.0
                    continue
                areas[idx] = gm.cell_area_m2(nx0, nx1, ny0, ny1)
                # optical: a smooth ramp through the DORI threshold rather than a cliff
                ppm = gm.px_per_m(z)
                optical = float(np.clip(ppm / need_ppm, 0.0, 1.0)) ** 0.5
                # geometric: is this cell inside any shadow?
                occ = 0.0
                nxc = (nx0 + nx1) / 2.0
                for sh in shadows:
                    p = sh["polygon"]
                    if p[0][0] <= nxc <= p[1][0] and p[0][1] <= nyc <= p[2][1]:
                        occ = 1.0
                        break
                q = float(quality[cy, cx])
                mort, ok = mortality_rate(m.enter.get(idx, 0), m.die.get(idx, 0),
                                          m.born.get(idx, 0), min_samples)
                if not ok:
                    mort = 0.0
                seen = ((1.0 - wg * occ)
                        * (1.0 - wo * (1.0 - optical))
                        * (1.0 - wr * (1.0 - q))
                        * (1.0 - we * mort))
                unseen[idx] = float(np.clip(1.0 - seen, 0.0, 1.0))
                if indet[cy, cx] > 0.5:
                    unseen[idx] = max(unseen[idx], 0.5)

        total_area = float(areas.sum())
        if total_area > 0:
            seen_area = float((areas * (1.0 - unseen)).sum())
            percent = 100.0 * seen_area / total_area
        else:
            percent = 0.0

        bands = []
        for t in DORI_ORDER:
            z = gm.range_for_px_per_m(DORI[t])
            bands.append({"task": t, "px_per_m": round(DORI[t], 1),
                          "range_m": round(float(z), 1), "y": round(gm.row_at(z), 4)})

        return {
            "cam": cam, "sid": str(source_id), "task": task,
            "target_height_m": round(target_h, 2),
            "percent": round(percent, 1),
            "fov_deg": round(float(fov_deg), 1),
            "grid": [gw, gh],
            "unseen": [round(float(v), 3) for v in unseen],
            "cells_m2": round(total_area / max(1, int((areas > 0).sum())), 2),
            "shadows": [{"id": s["id"], "polygon": s["polygon"], "occluder": s["occluder"],
                         "persistent": bool(s.get("persistent")),
                         "z_near": s["z_near"], "z_far": s["z_far"],
                         "height_m": s["height_m"]} for s in shadows],
            "bands": bands,
            "horizon_y": round(gm.horizon_ny, 4),
            "camera_height_m": round(cam_h, 2),
            "pitch_deg": round(math.degrees(pitch), 1),
            "scale_estimated": True,
            "ts": time.time() * 1000.0,
        }

    # -- persistence + blind spots -----------------------------------------------------------
    def _track_persistence(self, source_id: int, shadows: list[dict]) -> None:
        """A shadow seen in most samples over a long window is furniture, not traffic."""
        acc = self._occ_hits.setdefault(source_id, {})
        ratio = float(self._cfg("persist_ratio", 0.9))
        for sh in shadows:
            key = int(sh["polygon"][0][0] * 100) * 1000 + int(sh["polygon"][2][1] * 100)
            acc[key] = acc.get(key, 0) + 1
            sh["persistent"] = acc[key] >= max(3, int(6 * ratio))
        if len(acc) > 512:
            acc.clear()

    def blind_spots(self, source_id: int, coverage: dict | None = None) -> list[dict]:
        """The ranked, named, actionable list. Occlusion spots come from the live geometry;
        empirical spots come from the accumulation, which is why they survive a restart."""
        gw, gh = self.grid
        out: list[dict] = []
        stored = {int(r[0]): r for r in self.db.query(
            "SELECT id, kind, name, polygon, area_m2, persistent, first_seen, last_seen, events,"
            " channels, dismissed FROM blind_spots WHERE source_id = ?", (int(source_id),))}
        for row in stored.values():
            (bid, kind, name, poly, area, persist, first, last, events, chans, dismissed) = row
            try:
                polygon = json.loads(poly)
                channels = json.loads(chans or "{}")
            except Exception:
                continue
            out.append({
                "id": int(bid), "kind": kind, "name": name, "polygon": polygon,
                "area_m2": area, "persistent": bool(persist), "first_seen": first,
                "last_seen": last, "events": int(events), "channels": channels,
                "dismissed": bool(dismissed),
                "remedies": _remedies(kind, name, area),
            })
        # live occlusion wedges that are not yet recorded
        for sh in self._shadows.get(int(source_id)) or []:
            if not sh.get("persistent"):
                continue
            poly = sh["polygon"]
            if any(_poly_close(poly, s["polygon"]) for s in out):
                continue
            name = _name_for(poly, gw, gh)
            area = _shadow_area(sh)
            now = time.time()
            bid = self.db.execute(
                "INSERT INTO blind_spots (source_id, kind, name, polygon, area_m2, persistent,"
                " first_seen, last_seen, events, channels) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (int(source_id), "occlusion", name, json.dumps(poly), area, 1, now, now, 0,
                 json.dumps({"geometric": 1.0, "optical": 0.0, "radiometric": 0.0, "empirical": 0.0})))
            out.append({"id": int(bid), "kind": "occlusion", "name": name, "polygon": poly,
                        "area_m2": area, "persistent": True, "first_seen": now, "last_seen": now,
                        "events": 0,
                        "channels": {"geometric": 1.0, "optical": 0.0, "radiometric": 0.0,
                                     "empirical": 0.0},
                        "dismissed": False, "remedies": _remedies("occlusion", name, area)})
        # empirical spots: clusters of high-mortality cells
        m = self._mort(int(source_id))
        min_samples = int(self._cfg("min_cell_samples", 20))
        hot = []
        for cell, ne in m.enter.items():
            rate, ok = mortality_rate(ne, m.die.get(cell, 0), m.born.get(cell, 0), min_samples)
            if ok and rate > 0.28:
                hot.append((cell, rate, ne))
        hot.sort(key=lambda t: -t[1] * t[2])
        for cell, rate, ne in hot[:6]:
            cy, cx = divmod(cell, gw)
            poly = [[cx / gw, cy / gh], [(cx + 1) / gw, cy / gh],
                    [(cx + 1) / gw, (cy + 1) / gh], [cx / gw, (cy + 1) / gh]]
            if any(_poly_close(poly, s["polygon"]) for s in out):
                continue
            name = _name_for(poly, gw, gh)
            out.append({
                "id": -cell - 1, "kind": "empirical", "name": name, "polygon": poly,
                "area_m2": None, "persistent": True, "first_seen": 0.0, "last_seen": time.time(),
                "events": int(m.die.get(cell, 0)),
                "channels": {"geometric": 0.0, "optical": 0.0, "radiometric": 0.0,
                             "empirical": round(rate, 3)},
                "dismissed": False, "remedies": _remedies("empirical", name, None),
            })
        out.sort(key=lambda s: -( (s.get("area_m2") or 1.0) * (1 + s.get("events", 0)) ))
        return out


# ── helpers ─────────────────────────────────────────────────────────────────────────────────

def _foreground(disp01: np.ndarray) -> np.ndarray:
    """Standing-object mask. Imported lazily so this module stays importable without the
    spatial stack present (the geometry tests do not need it)."""
    try:
        from . import spatial as _sp
        return _sp.foreground_mask(disp01)
    except Exception:
        du8 = (np.clip(disp01, 0, 1) * 255).astype(np.uint8)
        bg = cv2.morphologyEx(du8, cv2.MORPH_OPEN,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        return (((du8.astype(np.int16) - bg.astype(np.int16)) > 20).astype(np.uint8) * 255)


def _poly_close(a: list, b: list, tol: float = 0.06) -> bool:
    if not a or not b:
        return False
    ax = sum(p[0] for p in a) / len(a); ay = sum(p[1] for p in a) / len(a)
    bx = sum(p[0] for p in b) / len(b); by = sum(p[1] for p in b) / len(b)
    return abs(ax - bx) < tol and abs(ay - by) < tol


def _shadow_area(sh: dict) -> float | None:
    zn, zf = sh.get("z_near"), sh.get("z_far")
    if zn is None or zf is None:
        return None
    poly = sh["polygon"]
    width_frac = abs(poly[1][0] - poly[0][0])
    return round(float(width_frac * (zf - zn) * (zf + zn) * 0.5), 1)


def _name_for(poly: list, gw: int, gh: int) -> str:
    cx = sum(p[0] for p in poly) / len(poly)
    cy = sum(p[1] for p in poly) / len(poly)
    ns = "FAR" if cy < 0.4 else ("MID" if cy < 0.72 else "NEAR")
    ew = "LEFT" if cx < 0.36 else ("RIGHT" if cx > 0.64 else "CENTRE")
    return f"{ns} {ew}"


def _remedies(kind: str, name: str, area: float | None) -> list[dict]:
    """Concrete, ranked next actions. Vague advice is worse than none, so each one names the
    thing to change and, where the geometry allows it, what that would recover."""
    a = f"~{area:.0f} m2" if area else "this area"
    if kind == "occlusion":
        return [
            {"text": f"MOVE OR REMOVE THE OBJECT AT {name}", "recovers_m2": area},
            {"text": f"A SECOND CAMERA OPPOSITE {name} WOULD SEE BEHIND IT", "recovers_m2": area},
            {"text": "RAISE THE CAMERA: A HIGHER MOUNT SHORTENS EVERY SHADOW", "recovers_m2": None},
        ]
    if kind == "resolution":
        return [
            {"text": "NARROW THE FIELD OF VIEW OR FIT A LONGER LENS", "recovers_m2": area},
            {"text": f"A CAMERA CLOSER TO {name} WOULD MEET THE TASK", "recovers_m2": area},
        ]
    if kind == "radiometric":
        return [
            {"text": f"ADD LIGHT AT {name}", "recovers_m2": area},
            {"text": "CHECK THE LENS FOR DIRT OR CONDENSATION", "recovers_m2": None},
            {"text": "REVIEW THE EXPOSURE: THIS REGION IS CLIPPING", "recovers_m2": None},
        ]
    return [
        {"text": f"TRACKS KEEP ENDING AT {name}: LOOK FOR AN OCCLUDER OR A LIGHTING EDGE",
         "recovers_m2": area},
        {"text": f"REPOSITION TO COVER {a} OF LOST GROUND", "recovers_m2": area},
    ]
