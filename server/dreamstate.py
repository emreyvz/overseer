"""DREAMSTATE — a per-camera expectation model.

The whole detection stack rests on one assumption: that somebody named the thing before it could
be found. DREAMSTATE inverts that. It learns what this place normally looks like at this hour and
reports, in calibrated sigma, where reality departs from it. It never classifies, and its output
is called DIVERGENCE everywhere, never THREAT: it can tell you something is off, and it cannot
tell you what.

Design notes that matter more than the maths:

**No dense optical flow.** The obvious feature set for "what is happening here" is flow, and the
obvious implementation is a Farneback pass. That pass was measured at ~340 ms on this pipeline
and was the reason analysis sat at 3 fps. Instead the features are built from things the backend
already has or can produce for almost nothing: the running background plate (already maintained
for the 3D view), a temporal difference, a Scharr edge response on a quarter-scale grey frame,
and detector occupancy. Cell means come free from an INTER_AREA resize.

**Robust, forgetting statistics.** Per cell and per time bucket, an online median and MAD updated
by stochastic approximation. Constant memory, immune to the very events it must detect, and it
forgets slowly, so seasonal drift is learned while an afternoon's change stays surprising.

**Global common-mode rejection.** A cloud crossing the sun, an auto-exposure step or the lights
coming on raise the residual in EVERY cell at once. Subtracting the frame-global residual median
before localisation is what separates a working detector from one that fires on every cloud. It
is one line and it is the difference between shipping and not.

**Three conditions to fire.** Magnitude, spatial coherence and temporal persistence. Any one of
them alone is a false-positive generator.
"""
from __future__ import annotations

import json
import math
import struct
import time
from typing import Any

import cv2
import numpy as np

FEATURES = ("plate", "motion", "edge", "occupancy", "intensity")
NF = len(FEATURES)
BUCKET_NAMES = ("NIGHT", "DAWN", "MORNING", "MIDDAY", "AFTERNOON", "DUSK")
_EPS = 1e-6


def time_bucket(ts: float, buckets: int = 6) -> int:
    hour = time.localtime(ts).tm_hour
    return int(hour * buckets / 24) % buckets


# ── robust online statistics ────────────────────────────────────────────────────────────────

class RobustStat:
    """Online median and MAD by stochastic approximation.

    A running mean and variance would be poisoned by exactly the events this model exists to
    find; a stored window would cost memory per cell per bucket. This costs two floats, is
    robust to outliers by construction, and its step size doubles as the forgetting factor.
    """

    __slots__ = ("median", "mad", "n")

    def __init__(self, median: float = 0.0, mad: float = 0.0, n: float = 0.0) -> None:
        self.median = float(median)
        self.mad = float(mad)
        self.n = float(n)

    def update(self, x: float, eta: float = 0.02) -> None:
        if self.n < 1:
            self.median, self.mad, self.n = float(x), 0.0, 1.0
            return
        # warm-up uses a larger step so a fresh cell converges in minutes rather than hours
        step = eta if self.n > 200 else max(eta, 1.0 / (self.n + 1.0))
        scale = max(self.mad, 1e-3)
        self.median += step * scale * (1.0 if x > self.median else -1.0 if x < self.median else 0.0)
        self.mad += step * (abs(x - self.median) - self.mad)
        self.n += 1.0

    def sigma(self, x: float) -> float:
        """Robust z-score. 1.4826 converts MAD to a normal-consistent standard deviation."""
        if self.n < 20:
            return 0.0                      # not enough evidence to call anything surprising
        s = 1.4826 * self.mad
        if s < 1e-4:
            return 0.0                      # a perfectly still cell: no scale, no claim
        return abs(x - self.median) / s


class CellModel:
    """One cell's expectation, one entry per feature."""

    __slots__ = ("stats",)

    def __init__(self) -> None:
        self.stats = [RobustStat() for _ in range(NF)]

    @property
    def n(self) -> float:
        return self.stats[0].n

    def update(self, vals: np.ndarray, eta: float) -> None:
        for i in range(NF):
            self.stats[i].update(float(vals[i]), eta)

    def sigma(self, vals: np.ndarray) -> float:
        """Median across features, not max: one noisy feature must not carry the cell."""
        zs = sorted(self.stats[i].sigma(float(vals[i])) for i in range(NF))
        return zs[NF // 2]

    def pack(self) -> bytes:
        out = []
        for s in self.stats:
            out.extend((s.median, s.mad, s.n))
        return struct.pack(f"<{NF * 3}f", *out)

    @classmethod
    def unpack(cls, blob: bytes) -> "CellModel":
        c = cls()
        try:
            vals = struct.unpack(f"<{NF * 3}f", blob)
        except Exception:
            return c
        for i in range(NF):
            c.stats[i] = RobustStat(vals[i * 3], vals[i * 3 + 1], vals[i * 3 + 2])
        return c


# ── feature extraction ──────────────────────────────────────────────────────────────────────

def cell_features(bgr: np.ndarray, prev_grey: np.ndarray | None, plate: np.ndarray | None,
                  dets: list[dict], grid: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """Per-cell feature grid, and the quarter-scale grey frame to carry forward.

    Returns (features [gh, gw, NF], grey). Every term is either already computed elsewhere in the
    pipeline or costs one cheap OpenCV call; nothing here runs dense flow.
    """
    gw, gh = grid
    h, w = bgr.shape[:2]
    small = cv2.resize(bgr, (max(gw * 4, 64), max(gh * 4, 36)), interpolation=cv2.INTER_AREA)
    grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32)

    feats = np.zeros((gh, gw, NF), np.float32)
    # 0 — departure from the learned background plate: the single most informative channel, and
    #     the plate is already maintained for the 3D view, so it is free here.
    if plate is not None and plate.shape[:2] == bgr.shape[:2]:
        pl = cv2.resize(plate.astype(np.uint8), (small.shape[1], small.shape[0]),
                        interpolation=cv2.INTER_AREA)
        pg = cv2.cvtColor(pl, cv2.COLOR_BGR2GRAY).astype(np.float32)
        feats[:, :, 0] = cv2.resize(np.abs(grey - pg), (gw, gh), interpolation=cv2.INTER_AREA)
    # 1 — temporal difference: one subtract
    if prev_grey is not None and prev_grey.shape == grey.shape:
        feats[:, :, 1] = cv2.resize(np.abs(grey - prev_grey), (gw, gh), interpolation=cv2.INTER_AREA)
    # 2 — edge energy: structure appearing or disappearing (a pallet, an open door, graffiti)
    edge = np.abs(cv2.Scharr(grey, cv2.CV_32F, 1, 0)) + np.abs(cv2.Scharr(grey, cv2.CV_32F, 0, 1))
    feats[:, :, 2] = cv2.resize(edge, (gw, gh), interpolation=cv2.INTER_AREA) / 16.0
    # 3 — detector occupancy: what the rest of the stack believes is here
    occ = np.zeros((gh, gw), np.float32)
    for d in dets:
        b = d.get("bbox")
        if not b:
            continue
        x0 = int(max(0, min(gw - 1, b[0] * gw)))
        y0 = int(max(0, min(gh - 1, b[1] * gh)))
        x1 = int(max(0, min(gw, (b[0] + b[2]) * gw + 1)))
        y1 = int(max(0, min(gh, (b[1] + b[3]) * gh + 1)))
        occ[y0:y1, x0:x1] = 1.0
    feats[:, :, 3] = occ
    # 4 — mean intensity: catches lights, screens, fires, and anything that changes brightness
    feats[:, :, 4] = cv2.resize(grey, (gw, gh), interpolation=cv2.INTER_AREA) / 255.0
    return feats, grey


def common_mode(sig: np.ndarray) -> np.ndarray:
    """Strip the global component from a per-cell sigma field.

    A cloud, an exposure step or the lights coming on lift EVERY cell at once. Rescaling against
    the frame's own median and MAD leaves a global change scoring ~0 everywhere and a local one
    intact. Without this the model fires on the weather and is unusable.
    """
    flat = sig.reshape(-1)
    med = float(np.median(flat))
    mad = float(np.median(np.abs(flat - med)))
    scale = 1.4826 * mad
    if scale < 1e-3:
        return np.maximum(0.0, sig - med)
    return np.maximum(0.0, (sig - med) / scale)


# ── firing ──────────────────────────────────────────────────────────────────────────────────

def blobs(mask: np.ndarray, min_cells: int) -> list[dict]:
    """Connected components of qualifying cells. A single hot cell is noise; a coherent patch of
    them is a thing."""
    m = mask.astype(np.uint8)
    n, lab, stats, _cent = cv2.connectedComponentsWithStats(m, connectivity=4)
    gh, gw = mask.shape
    out = []
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        if area < min_cells:
            continue
        # plain floats, not numpy scalars: these go straight into json.dumps and over the socket
        x0, y0 = x / gw, y / gh
        x1, y1 = (x + bw) / gw, (y + bh) / gh
        out.append({
            "cells": [int(c) for c in np.flatnonzero((lab == i).reshape(-1))],
            "bbox": [float(x0), float(y0), float(bw / gw), float(bh / gh)],
            "polygon": [[float(x0), float(y0)], [float(x1), float(y0)],
                        [float(x1), float(y1)], [float(x0), float(y1)]],
            "area": area,
        })
    return out


def iou(a: list[float], b: list[float]) -> float:
    ax0, ay0, ax1, ay1 = a[0], a[1], a[0] + a[2], a[1] + a[3]
    bx0, by0, bx1, by1 = b[0], b[1], b[0] + b[2], b[1] + b[3]
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > _EPS else 0.0


# ── the engine ──────────────────────────────────────────────────────────────────────────────

class DreamEngine:
    """Learns, watches, and fires. One instance per backend; state is keyed by source."""

    def __init__(self, db: Any, config: Any) -> None:
        self.db = db
        self.config = config
        gw, gh = self._cfg("grid", [24, 14])
        self.grid: tuple[int, int] = (int(gw), int(gh))
        self.buckets = int(self._cfg("buckets", 6))
        self.cells: dict[tuple[int, int, int], CellModel] = {}   # (sid, bucket, cell)
        self.muted: dict[int, set[int]] = {}
        self._prev_grey: dict[int, np.ndarray] = {}
        self._loaded: set[int] = set()
        self._open: dict[int, list[dict]] = {}          # candidate blobs being timed
        self._last_obs: dict[int, float] = {}
        self._last_flush = 0.0
        self._dirty: set[tuple[int, int, int]] = set()
        self._pulse: dict[int, tuple[float, float, float]] = {}   # sid -> (minute, peak, sum/n)
        self._sigma: dict[int, np.ndarray] = {}
        self.stale: dict[int, bool] = {}
        # Operator-tuned sensitivity, set live from the console and persisted in the settings
        # table. None means "use the configured default".
        self.threshold: float | None = None
        try:
            saved = db.get_setting("dream.sigma_threshold")
            if saved is not None:
                self.threshold = float(saved)
        except Exception:
            self.threshold = None

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            return self.config.get(f"dream.{key}", default)
        except Exception:
            return default

    # -- persistence -------------------------------------------------------------------------
    def load(self, sid: int) -> None:
        if sid in self._loaded:
            return
        self._loaded.add(sid)
        try:
            rows = self.db.query(
                "SELECT bucket, cell, stats FROM dream_state WHERE source_id = ?", (int(sid),))
        except Exception:
            return
        for bucket, cell, blob in rows:
            self.cells[(int(sid), int(bucket), int(cell))] = CellModel.unpack(blob)
        try:
            mrows = self.db.query("SELECT cell FROM dream_mute WHERE source_id = ?", (int(sid),))
            self.muted[int(sid)] = {int(r[0]) for r in mrows}
        except Exception:
            self.muted[int(sid)] = set()

    def flush(self, sid: int) -> None:
        if not self._dirty:
            return
        now = time.time()
        rows = []
        for key in list(self._dirty):
            if key[0] != sid:
                continue
            m = self.cells.get(key)
            if m is None:
                continue
            rows.append((key[0], key[1], key[2], m.pack(), int(m.n), now))
            self._dirty.discard(key)
        if not rows:
            return
        try:
            self.db.execute_many(
                "INSERT INTO dream_state (source_id, bucket, cell, stats, n, updated_at)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT(source_id, bucket, cell) DO UPDATE SET"
                " stats=excluded.stats, n=excluded.n, updated_at=excluded.updated_at", rows)
        except Exception:
            pass

    # -- the observation ---------------------------------------------------------------------
    def observe(self, sid: int, bgr: np.ndarray, plate: np.ndarray | None, dets: list[dict],
                now: float) -> dict | None:
        """One tick. Returns a divergence dict when all three firing conditions are met."""
        rate = float(self._cfg("rate_hz", 2.0))
        if now - self._last_obs.get(sid, 0.0) < 1.0 / max(0.2, rate):
            return None
        self._last_obs[sid] = now
        self.load(sid)
        gw, gh = self.grid
        bucket = time_bucket(now, self.buckets)
        feats, grey = cell_features(bgr, self._prev_grey.get(sid), plate, dets, self.grid)
        self._prev_grey[sid] = grey

        min_obs = float(self._cfg("min_observations", 300))
        sig = np.zeros((gh, gw), np.float32)
        mature = 0
        for cy in range(gh):
            for cx in range(gw):
                cell = cy * gw + cx
                key = (sid, bucket, cell)
                m = self.cells.get(key)
                if m is None:
                    m = CellModel()
                    self.cells[key] = m
                if m.n >= min_obs:
                    sig[cy, cx] = m.sigma(feats[cy, cx])
                    mature += 1
        # learn from every frame, including the surprising ones: a robust median is not moved by
        # a single outlier, and refusing to learn while surprised would freeze the model forever
        eta = self._eta()
        for cy in range(gh):
            for cx in range(gw):
                key = (sid, bucket, cy * gw + cx)
                self.cells[key].update(feats[cy, cx], eta)
                self._dirty.add(key)

        if now - self._last_flush > 60.0:
            self._last_flush = now
            self.flush(sid)

        if mature < (gw * gh) * 0.25:
            self._sigma[sid] = sig
            return None                       # this hour is UNLEARNED: never fire from it

        z = common_mode(sig)
        muted = self.muted.get(sid, set())
        if muted:
            for c in muted:
                z[c // gw, c % gw] = 0.0
        self._sigma[sid] = z
        self._record_pulse(sid, z, now)
        return self._fire(sid, z, bucket, now)

    def _eta(self) -> float:
        """Step size, derived from the configured forgetting half-life at the observation rate."""
        half = max(1.0, float(self._cfg("forget_half_life_days", 21)))
        rate = float(self._cfg("rate_hz", 2.0))
        obs_per_half_life = half * 86400.0 * rate
        return float(min(0.05, max(0.002, 3.0 / math.sqrt(max(1.0, obs_per_half_life)))))

    def _record_pulse(self, sid: int, z: np.ndarray, now: float) -> None:
        minute = math.floor(now / 60.0) * 60.0
        peak, mean = float(z.max()), float(z.mean())
        cur = self._pulse.get(sid)
        if cur is None or cur[0] != minute:
            if cur is not None:
                try:
                    self.db.execute(
                        "INSERT INTO dream_pulse (source_id, minute_ts, peak, mean)"
                        " VALUES (?,?,?,?) ON CONFLICT(source_id, minute_ts) DO UPDATE SET"
                        " peak=excluded.peak, mean=excluded.mean",
                        (int(sid), cur[0], cur[1], cur[2]))
                except Exception:
                    pass
            self._pulse[sid] = (minute, peak, mean)
        else:
            self._pulse[sid] = (minute, max(cur[1], peak), (cur[2] + mean) / 2.0)

    def _fire(self, sid: int, z: np.ndarray, bucket: int, now: float) -> dict | None:
        thr = float(self.threshold if self.threshold is not None
                    else self._cfg("sigma_threshold", 5.0))
        min_cells = int(self._cfg("min_cells", 3))
        persist = float(self._cfg("persist_s", 2.0))
        found = blobs(z >= thr, min_cells)
        open_list = self._open.setdefault(sid, [])
        # match this frame's blobs to the ones already being timed
        for b in found:
            peak = float(max(z.reshape(-1)[c] for c in b["cells"]))
            match = None
            for o in open_list:
                if iou(o["bbox"], b["bbox"]) >= 0.4:
                    match = o
                    break
            if match is None:
                open_list.append({"since": now, "last": now, "bbox": b["bbox"],
                                  "cells": b["cells"], "polygon": b["polygon"],
                                  "peak": peak, "area_sigma_s": 0.0, "fired": False})
            else:
                dt = now - match["last"]
                match["last"] = now
                match["bbox"] = b["bbox"]
                match["cells"] = b["cells"]
                match["polygon"] = b["polygon"]
                match["peak"] = max(match["peak"], peak)
                match["area_sigma_s"] += peak * len(b["cells"]) * dt
        # retire the candidates that have gone quiet
        self._open[sid] = [o for o in open_list if now - o["last"] < 1.5]
        for o in self._open[sid]:
            if o["fired"] or now - o["since"] < persist:
                continue
            o["fired"] = True
            return {
                "source_id": sid, "ts": now * 1000.0,
                "peak_sigma": round(o["peak"], 2),
                "area_sigma_s": round(o["area_sigma_s"], 2),
                "blob": o["polygon"], "cells": o["cells"], "tier": "A",
                "bucket": bucket,
            }
        return None

    # -- state for the UI --------------------------------------------------------------------
    def status(self, sid: int, cam: str) -> dict:
        self.load(sid)
        gw, gh = self.grid
        min_obs = float(self._cfg("min_observations", 300))
        bucket = time_bucket(time.time(), self.buckets)
        buckets = []
        for b in range(self.buckets):
            ns = [m.n for (s, bk, _c), m in self.cells.items() if s == sid and bk == b]
            n = float(np.median(ns)) if ns else 0.0
            buckets.append({"name": BUCKET_NAMES[b % len(BUCKET_NAMES)], "n": int(n),
                            "maturity": round(min(1.0, n / min_obs), 3)})
        z = self._sigma.get(sid)
        cells = [round(float(v), 2) for v in (z.reshape(-1) if z is not None
                                              else np.zeros(gw * gh, np.float32))]
        return {
            "cam": cam, "tier": "A", "bucket": bucket, "buckets": buckets,
            "maturity": buckets[bucket]["maturity"] if buckets else 0.0,
            "sigma": round(float(z.max()) if z is not None else 0.0, 2),
            "cells": cells, "grid": [gw, gh],
            "stale": bool(self.stale.get(sid)),
            "muted": sorted(self.muted.get(sid, set())),
            "threshold": float(self._cfg("sigma_threshold", 5.0)),
        }

    def pulse(self, sid: int, hours: int = 24) -> list[dict]:
        since = time.time() - hours * 3600
        try:
            rows = self.db.query(
                "SELECT minute_ts, peak, mean FROM dream_pulse WHERE source_id = ? AND"
                " minute_ts >= ? ORDER BY minute_ts", (int(sid), since))
        except Exception:
            return []
        return [{"t": float(r[0]) * 1000.0, "peak": round(float(r[1]), 2),
                 "mean": round(float(r[2]), 3)} for r in rows]

    def mute(self, sid: int, cells: list[int], from_hour: int = 0, to_hour: int = 24) -> list[int]:
        s = self.muted.setdefault(int(sid), set())
        now = time.time()
        for c in cells:
            c = int(c)
            if c in s:
                s.discard(c)
                self.db.execute("DELETE FROM dream_mute WHERE source_id = ? AND cell = ?",
                                (int(sid), c))
            else:
                s.add(c)
                self.db.execute(
                    "INSERT INTO dream_mute (source_id, cell, from_hour, to_hour, created_at)"
                    " VALUES (?,?,?,?,?) ON CONFLICT(source_id, cell) DO UPDATE SET"
                    " from_hour=excluded.from_hour, to_hour=excluded.to_hour",
                    (int(sid), c, int(from_hour), int(to_hour), now))
        return sorted(s)

    def reset(self, sid: int) -> None:
        """Forget this camera entirely. Used when the view has changed beyond re-registration."""
        for key in [k for k in self.cells if k[0] == sid]:
            self.cells.pop(key, None)
        self._open.pop(sid, None)
        self._sigma.pop(sid, None)
        self.stale[sid] = False
        try:
            self.db.execute("DELETE FROM dream_state WHERE source_id = ?", (int(sid),))
        except Exception:
            pass

    # -- divergence records ------------------------------------------------------------------
    def record(self, div: dict, snapshot: str | None, triage: str | None = None) -> int:
        try:
            return int(self.db.execute(
                "INSERT INTO dream_divergence (source_id, ts, peak_sigma, area_sigma_s, blob,"
                " cells, snapshot_path, triage, tier) VALUES (?,?,?,?,?,?,?,?,?)",
                (int(div["source_id"]), float(div["ts"]) / 1000.0, float(div["peak_sigma"]),
                 float(div["area_sigma_s"]), json.dumps(div["blob"]), json.dumps(div["cells"]),
                 snapshot, triage, div.get("tier", "A"))))
        except Exception:
            return 0

    def divergences(self, sid: int | None = None, limit: int = 100) -> list[dict]:
        q = ("SELECT id, source_id, ts, peak_sigma, area_sigma_s, blob, cells, snapshot_path,"
             " verdict, triage, tier FROM dream_divergence")
        params: list[Any] = []
        if sid is not None:
            q += " WHERE source_id = ?"
            params.append(int(sid))
        q += " ORDER BY ts DESC LIMIT ?"
        params.append(int(limit))
        try:
            rows = self.db.query(q, params)
        except Exception:
            return []
        out = []
        for r in rows:
            try:
                blob = json.loads(r[5]); cells = json.loads(r[6])
            except Exception:
                blob, cells = [], []
            out.append({
                "id": int(r[0]), "cam": str(r[1]), "ts": float(r[2]) * 1000.0,
                "peak_sigma": float(r[3]), "area_sigma_s": float(r[4]),
                "blob": blob, "cells": cells, "snapshot": r[7],
                "verdict": r[8], "triage": r[9], "tier": r[10],
            })
        return out

    def verdict(self, div_id: int, verdict: str | None) -> dict:
        self.db.execute("UPDATE dream_divergence SET verdict = ?, verdict_ts = ? WHERE id = ?",
                        (verdict, time.time(), int(div_id)))
        return {"ok": True}
