"""GRAIN — the behavioural grain of a place.

Every site has an unwritten choreography: which way people go here, how fast, where they pause,
which turns are ordinary and which are not. GRAIN learns that distribution from the site's own
tracks and scores each new subject against it, so a person doing something individually innocuous
in an arrangement this place has never seen becomes visible.

Two rules govern the whole module.

**Movement only.** No appearance, identity or demographic feature ever reaches the model. Not
clothing colour, not height, not gait, not face, not plate. A model trained on "who normally
walks here" would learn to flag people who do not look like the regulars, which is both unjust
and useless. `assert_movement_only` enforces this at the boundary and tests/test_grain.py asserts
it raises; it is a code-level guarantee, not a policy note.

**Three states, not two.** ORDINARY, UNUSUAL, and UNJUDGED. A cell the model has barely seen
returns UNJUDGED and never alerts. Collapsing ignorance into "anomalous" is the single reason
behavioural analytics has the reputation it has.

Tier A (this file) is a spatially-conditioned Markov field: per cell, a circular histogram of
headings, a log-normal speed, a Dirichlet transition distribution and an exponential dwell. It
trains in seconds, needs no GPU, and its log-likelihood decomposes exactly into per-factor terms,
so the explanation IS the score rather than a story told about it afterwards.
"""
from __future__ import annotations

import bisect
import json
import math
import struct
import time
from collections import deque
from typing import Any, Callable, Iterable

import numpy as np

#: How many recent track scores are kept in memory per camera to estimate percentiles. Large
#: enough that the distribution is stable, small enough that the sorted snapshot is trivial.
SAMPLE = 4000
#: Re-sort the snapshot only every N appends; sorting 4k floats is ~0.3 ms and this happens
#: once per this many track closes.
RESORT_EVERY = 64

# Anything that describes what a subject LOOKS like, rather than what they DO. Presence of any
# of these in a sample dict is a programming error, not a value to ignore quietly.
FORBIDDEN_KEYS = frozenset({
    "upper_color", "lower_color", "colour", "color", "height", "height_cm", "stature",
    "accessory", "accessories", "skin", "skin_fraction", "bodytype", "body_type", "make",
    "plate", "subtype", "face", "gait", "embedding", "descriptor", "attrs", "snapshot",
    "subject_uid", "label", "name",
})

HEADING_BINS = 16
TRANS_OUT = 10                # 8 neighbours + stay + terminate
_ALPHA = 0.5                  # Dirichlet / Laplace prior: an unseen option is unlikely, not impossible
_EPS = 1e-9

BUCKET_NAMES = ("NIGHT", "DAWN", "MORNING", "MIDDAY", "AFTERNOON", "DUSK")
FACTORS = ("path", "speed", "heading", "dwell")


def assert_movement_only(sample: dict) -> None:
    """Raise if a sample carries anything about appearance or identity.

    The guard exists at the extractor boundary rather than deeper in, because by the time a
    feature vector has been built it is too late to tell what went into it.
    """
    bad = FORBIDDEN_KEYS.intersection(sample)
    if bad:
        raise ValueError(
            "GRAIN scores movement only; appearance/identity keys are not permitted: "
            + ", ".join(sorted(bad)))


def time_bucket(ts: float, buckets: int = 6) -> int:
    """Hour of day folded into `buckets`. Deliberately coarse: finer buckets need proportionally
    more data before any of them mature."""
    hour = time.localtime(ts).tm_hour
    return int(hour * buckets / 24) % buckets


def density_bucket(n_concurrent: int) -> int:
    """Sparse / normal / crowded. Deviating around other people is not an anomaly, so the model
    is conditioned on how busy the scene is."""
    return 0 if n_concurrent <= 2 else (1 if n_concurrent <= 7 else 2)


# ── per-cell statistics ─────────────────────────────────────────────────────────────────────

class CellStats:
    """Movement distributions for one (cell, bucket, density, class).

    Everything is an online accumulator with a forgetting factor, so a site that genuinely
    changes is learned rather than argued with, while a change that happens in an afternoon
    still reads as a surprise.
    """

    __slots__ = ("n", "heading", "slog_sum", "slog_sq", "slog_n", "trans", "dwell_sum", "dwell_n")

    def __init__(self) -> None:
        self.n = 0.0
        self.heading = np.zeros(HEADING_BINS, np.float64)
        self.slog_sum = 0.0
        self.slog_sq = 0.0
        self.slog_n = 0.0
        self.trans = np.zeros(TRANS_OUT, np.float64)
        self.dwell_sum = 0.0
        self.dwell_n = 0.0

    # -- accumulation ------------------------------------------------------------------------
    def add(self, heading: float, speed: float, out: int, dwell: float, w: float = 1.0) -> None:
        self.n += w
        self.heading[_hbin(heading)] += w
        if speed > _EPS:
            ls = math.log(speed)
            self.slog_sum += ls * w
            self.slog_sq += ls * ls * w
            self.slog_n += w
        if 0 <= out < TRANS_OUT:
            self.trans[out] += w
        if dwell > 0:
            self.dwell_sum += dwell * w
            self.dwell_n += w

    def decay(self, factor: float) -> None:
        self.n *= factor
        self.heading *= factor
        self.slog_sum *= factor
        self.slog_sq *= factor
        self.slog_n *= factor
        self.trans *= factor
        self.dwell_sum *= factor
        self.dwell_n *= factor

    # -- densities ---------------------------------------------------------------------------
    def log_heading(self, heading: float) -> float:
        p = (self.heading[_hbin(heading)] + _ALPHA) / (self.heading.sum() + _ALPHA * HEADING_BINS)
        return math.log(max(p, _EPS))

    def log_speed(self, speed: float) -> float:
        if self.slog_n < 3 or speed <= _EPS:
            return math.log(1.0 / 8.0)                # uninformative, but finite
        mu = self.slog_sum / self.slog_n
        var = max(0.04, self.slog_sq / self.slog_n - mu * mu)   # floor: never claim certainty
        ls = math.log(speed)
        return -0.5 * math.log(2 * math.pi * var) - (ls - mu) ** 2 / (2 * var) - ls

    def log_trans(self, out: int) -> float:
        if not (0 <= out < TRANS_OUT):
            return math.log(_ALPHA / (self.trans.sum() + _ALPHA * TRANS_OUT))
        p = (self.trans[out] + _ALPHA) / (self.trans.sum() + _ALPHA * TRANS_OUT)
        return math.log(max(p, _EPS))

    def log_dwell(self, dwell: float) -> float:
        if self.dwell_n < 3:
            return math.log(1.0 / 4.0)
        mean = max(0.2, self.dwell_sum / self.dwell_n)
        lam = 1.0 / mean
        return math.log(lam) - lam * max(0.0, dwell)

    # -- UI summaries ------------------------------------------------------------------------
    def modal_heading(self) -> float:
        """Circular mean of the heading histogram (radians)."""
        ang = (np.arange(HEADING_BINS) + 0.5) * (2 * math.pi / HEADING_BINS)
        s = float((self.heading * np.sin(ang)).sum())
        c = float((self.heading * np.cos(ang)).sum())
        return math.atan2(s, c)

    def concentration(self) -> float:
        """Resultant length in [0,1]: how strongly this cell prefers one direction. This is what
        makes the field render as a current rather than as noise."""
        tot = float(self.heading.sum())
        if tot < 1e-6:
            return 0.0
        ang = (np.arange(HEADING_BINS) + 0.5) * (2 * math.pi / HEADING_BINS)
        s = float((self.heading * np.sin(ang)).sum()) / tot
        c = float((self.heading * np.cos(ang)).sum()) / tot
        return float(min(1.0, math.hypot(s, c)))

    def modal_speed(self) -> float:
        if self.slog_n < 1:
            return 0.0
        return float(math.exp(self.slog_sum / self.slog_n))

    def speed_hist(self, bins: int = 12) -> list[float]:
        """A visual stand-in for the log-normal, so the cell inspector shows a shape rather than
        two numbers."""
        if self.slog_n < 3:
            return [0.0] * bins
        mu = self.slog_sum / self.slog_n
        var = max(0.04, self.slog_sq / self.slog_n - mu * mu)
        sd = math.sqrt(var)
        xs = np.linspace(mu - 3 * sd, mu + 3 * sd, bins)
        pdf = np.exp(-0.5 * ((xs - mu) / sd) ** 2)
        tot = float(pdf.sum()) or 1.0
        return [float(v / tot) for v in pdf]

    # -- serialization -----------------------------------------------------------------------
    def pack(self) -> tuple[bytes, bytes, bytes, bytes]:
        heading = self.heading.astype(np.float32).tobytes()
        speed = struct.pack("<3f", self.slog_sum, self.slog_sq, self.slog_n)
        trans = self.trans.astype(np.float32).tobytes()
        dwell = struct.pack("<2f", self.dwell_sum, self.dwell_n)
        return heading, speed, trans, dwell

    @classmethod
    def unpack(cls, n: float, heading: bytes, speed: bytes, trans: bytes,
               dwell: bytes) -> "CellStats":
        c = cls()
        c.n = float(n)
        try:
            c.heading = np.frombuffer(heading, np.float32).astype(np.float64).copy()
            if c.heading.size != HEADING_BINS:
                c.heading = np.zeros(HEADING_BINS, np.float64)
            c.slog_sum, c.slog_sq, c.slog_n = struct.unpack("<3f", speed)
            c.trans = np.frombuffer(trans, np.float32).astype(np.float64).copy()
            if c.trans.size != TRANS_OUT:
                c.trans = np.zeros(TRANS_OUT, np.float64)
            c.dwell_sum, c.dwell_n = struct.unpack("<2f", dwell)
        except Exception:
            return cls()
        return c


def _hbin(heading: float) -> int:
    return int(((heading % (2 * math.pi)) / (2 * math.pi)) * HEADING_BINS) % HEADING_BINS


# ── trajectory features ─────────────────────────────────────────────────────────────────────

def resample(samples: list[dict], dt: float) -> list[dict]:
    """Uniform time resampling of a track. Detections arrive at whatever rate the analysis pass
    manages, and an unresampled trajectory would encode the pipeline's load rather than the
    subject's behaviour."""
    if len(samples) < 2:
        return list(samples)
    for s in samples:
        assert_movement_only(s)
    t0, t1 = samples[0]["t"], samples[-1]["t"]
    if t1 - t0 < dt:
        return [samples[0], samples[-1]]
    out: list[dict] = []
    j = 0
    t = t0
    while t <= t1 + 1e-6:
        while j + 1 < len(samples) - 1 and samples[j + 1]["t"] < t:
            j += 1
        a, b = samples[j], samples[min(j + 1, len(samples) - 1)]
        span = max(_EPS, b["t"] - a["t"])
        u = min(1.0, max(0.0, (t - a["t"]) / span))
        out.append({
            "t": t,
            "x": a["x"] + (b["x"] - a["x"]) * u,
            "y": a["y"] + (b["y"] - a["y"]) * u,
            "aspect": a.get("aspect", 0.4),
            "density": a.get("density", 0),
        })
        t += dt
    return out


def steps(path: list[dict], grid: tuple[int, int]) -> list[dict]:
    """Per-step movement features: cell, heading, speed, transition direction, dwell."""
    gw, gh = grid
    out: list[dict] = []
    dwell = 0.0
    for i in range(len(path) - 1):
        a, b = path[i], path[i + 1]
        dx, dy = b["x"] - a["x"], b["y"] - a["y"]
        dt = max(_EPS, b["t"] - a["t"])
        dist = math.hypot(dx, dy)
        speed = dist / dt
        heading = math.atan2(dy, dx) if dist > 1e-4 else (out[-1]["heading"] if out else 0.0)
        ca = _cell(a["x"], a["y"], gw, gh)
        cb = _cell(b["x"], b["y"], gw, gh)
        if ca == cb:
            dwell += dt
            direction = 8                                     # stay
        else:
            direction = _neighbour(ca, cb, gw)
            dwell = 0.0
        out.append({"cell": ca, "heading": heading, "speed": speed, "out": direction,
                    "dwell": dwell, "x": a["x"], "y": a["y"],
                    "density": int(a.get("density", 0))})
    if out:
        out[-1] = dict(out[-1], out=9)                        # terminate
    return out


def _cell(nx: float, ny: float, gw: int, gh: int) -> int:
    cx = min(gw - 1, max(0, int(nx * gw)))
    cy = min(gh - 1, max(0, int(ny * gh)))
    return cy * gw + cx


def _neighbour(a: int, b: int, gw: int) -> int:
    """Which of the 8 compass directions b lies in, relative to a.

    The displacement is reduced to its SIGN rather than requiring adjacency: at a realistic
    resample interval a brisk walker crosses two or three cells per step, and treating that as
    'not adjacent' would collapse ordinary walking into the same bucket as standing still.
    """
    ay, ax = divmod(a, gw)
    by, bx = divmod(b, gw)
    dx = 0 if bx == ax else (1 if bx > ax else -1)
    dy = 0 if by == ay else (1 if by > ay else -1)
    table = {(1, 0): 0, (1, -1): 1, (0, -1): 2, (-1, -1): 3,
             (-1, 0): 4, (-1, 1): 5, (0, 1): 6, (1, 1): 7}
    return table.get((dx, dy), 8)


def shape_vector(path: list[dict], n: int = 16) -> np.ndarray:
    """A translation-normalized 2n vector describing a trajectory's SHAPE, for precedent lookup.

    Anchored at the start and scaled by its own extent, so "walked in and stopped by the door"
    matches whether it happened at the left of frame or the right.
    """
    if len(path) < 2:
        return np.zeros(n * 2, np.float32)
    xs = np.array([p["x"] for p in path], np.float32)
    ys = np.array([p["y"] for p in path], np.float32)
    idx = np.linspace(0, len(xs) - 1, n)
    rx = np.interp(idx, np.arange(len(xs)), xs)
    ry = np.interp(idx, np.arange(len(ys)), ys)
    rx -= rx[0]
    ry -= ry[0]
    scale = float(max(1e-3, np.hypot(rx, ry).max()))
    return np.concatenate([rx / scale, ry / scale]).astype(np.float32)


# ── the engine ──────────────────────────────────────────────────────────────────────────────

class GrainEngine:
    """Accumulates the field, scores closed tracks, and answers the UI's questions."""

    def __init__(self, db: Any, config: Any,
                 occluded: Callable[[int, float, float], bool] | None = None) -> None:
        self.db = db
        self.config = config
        # FOG OF WAR coupling. A track that ends inside a known shadow did not vanish, it was
        # hidden, and counting that as a rare 'terminate' would poison the transition model AND
        # flag an innocent person. Without this link both features generate noise.
        self.occluded = occluded
        gw, gh = self._cfg("grid", [48, 27])
        self.grid: tuple[int, int] = (int(gw), int(gh))
        self.dt = float(self._cfg("dt", 0.5))
        self.buckets = int(self._cfg("buckets", 6))
        self.cells: dict[tuple[int, int, int, int, str], CellStats] = {}
        self.live: dict[str, dict] = {}          # det_id -> open track
        self.muted: dict[int, set[int]] = {}     # source_id -> muted cells
        self._loaded: set[int] = set()
        self._track_counts: dict[int, int] = {}
        self._dirty: set[tuple[int, int, int, int, str]] = set()
        self._last_flush = 0.0
        # In-memory score distributions, per source. These used to be SQL scans of grain_track,
        # which made every peek() linear in the size of the record: at 100k stored tracks, twenty
        # subjects in frame measured 4.7 SECONDS of CPU per second of video. A percentile is a
        # bisect into a sorted sample, so it belongs in memory.
        self._scores: dict[int, deque] = {}                    # source -> recent track scores
        self._raw: dict[int, dict[str, deque]] = {}            # source -> factor -> recent values
        self._sorted: dict[int, list[float]] = {}              # cached sorted snapshots
        self._sorted_raw: dict[int, dict[str, list[float]]] = {}
        self._since_sort: dict[int, int] = {}

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            return self.config.get(f"grain.{key}", default)
        except Exception:
            return default

    # -- persistence -------------------------------------------------------------------------
    def load(self, source_id: int) -> None:
        if source_id in self._loaded:
            return
        self._loaded.add(source_id)
        try:
            rows = self.db.query(
                "SELECT cell, bucket, density, cls, n, heading, speed, trans, dwell"
                " FROM grain_cell WHERE source_id = ?", (int(source_id),))
        except Exception:
            return
        for cell, bucket, density, cls, n, hd, sp, tr, dw in rows:
            if not (hd and sp and tr and dw):
                continue
            self.cells[(int(source_id), int(cell), int(bucket), int(density), str(cls))] = \
                CellStats.unpack(n, hd, sp, tr, dw)
        try:
            row = self.db.query("SELECT COUNT(*) FROM grain_track WHERE source_id = ?",
                                (int(source_id),))
            self._track_counts[int(source_id)] = int(row[0][0]) if row else 0
        except Exception:
            self._track_counts[int(source_id)] = 0
        # Rebuild the score distributions ONCE, here. Without this the percentiles reset to 50
        # after every restart and the calibration is silently un-learned.
        try:
            rows = self.db.query(
                "SELECT score, factors FROM grain_track WHERE source_id = ?"
                " ORDER BY id DESC LIMIT ?", (int(source_id), SAMPLE))
            for score, blob in reversed(rows):
                raw = {}
                try:
                    parsed = json.loads(blob)
                    raw = parsed.get("raw") or {} if isinstance(parsed, dict) else {}
                except Exception:
                    raw = {}
                self.remember(int(source_id), float(score), raw)
        except Exception:
            pass
        try:
            mrows = self.db.query("SELECT cell FROM grain_mute WHERE source_id = ?",
                                  (int(source_id),))
            self.muted[int(source_id)] = {int(r[0]) for r in mrows}
        except Exception:
            self.muted.setdefault(int(source_id), set())

    def flush(self, source_id: int) -> None:
        if not self._dirty:
            return
        now = time.time()
        rows = []
        for key in list(self._dirty):
            if key[0] != source_id:
                continue
            st = self.cells.get(key)
            if st is None:
                continue
            hd, sp, tr, dw = st.pack()
            rows.append((key[0], key[1], key[2], key[3], key[4], st.n, hd, sp, tr, dw, now))
            self._dirty.discard(key)
        if not rows:
            return
        try:
            self.db.execute_many(
                "INSERT INTO grain_cell (source_id, cell, bucket, density, cls, n, heading,"
                " speed, trans, dwell, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(source_id, cell, bucket, density, cls) DO UPDATE SET"
                " n=excluded.n, heading=excluded.heading, speed=excluded.speed,"
                " trans=excluded.trans, dwell=excluded.dwell, updated_at=excluded.updated_at",
                rows)
        except Exception:
            pass

    # -- live accumulation -------------------------------------------------------------------
    def observe(self, source_id: int, det_id: str, cls: str, nx: float, ny: float, ts: float,
                aspect: float = 0.4, density: int = 0) -> None:
        """One foot-point sample. Cheap by construction: an append and a dict write."""
        self.load(source_id)
        tr = self.live.get(det_id)
        if tr is None or tr["source_id"] != source_id:
            tr = {"source_id": source_id, "cls": cls, "samples": [], "start": ts}
            self.live[det_id] = tr
        tr["last"] = ts
        tr["samples"].append({"t": ts, "x": float(nx), "y": float(ny),
                              "aspect": float(aspect), "density": int(density)})
        if len(tr["samples"]) > 2400:                 # ~20 min at 2 Hz: bound a parked subject
            tr["samples"] = tr["samples"][-1200:]

    def peek(self, det_id: str, now: float, min_interval: float = 2.0) -> dict | None:
        """Score an OPEN track so far, so the live gauge has something to show.

        Waiting for a track to close before saying anything would make the whole feature a
        post-mortem. Throttled per track, and it never writes to the field: a subject still in
        frame has not finished being whatever they are.
        """
        tr = self.live.get(det_id)
        if tr is None:
            return None
        if now - tr.get("peeked", 0.0) < min_interval:
            return tr.get("peek")
        tr["peeked"] = now
        samples = tr["samples"]
        if len(samples) < 3:
            return None
        dur = float(samples[-1]["t"] - samples[0]["t"])
        if dur < float(self._cfg("min_track_s", 4.0)):
            return None
        sid = int(tr["source_id"])
        cls = str(tr.get("cls", "person"))
        path = resample(samples, self.dt)
        st = steps(path, self.grid)
        muted = self.muted.get(sid, set())
        st = [s for s in st[:-1] if s["cell"] not in muted]     # drop the synthetic terminate
        if not st:
            return None
        bucket = time_bucket(samples[0]["t"], self.buckets)
        scored = self.score(sid, cls, bucket, st)
        pct = self.percentile(sid, scored["score"])
        thr = float(self._cfg("percentile_threshold", 0.5))
        if not self.mature(sid) or scored.get("immature", 1.0) > 0.5:
            state = "unjudged"
        elif pct <= thr:
            state = "unusual"
        else:
            state = "ordinary"
        raw = scored["per_factor"]
        factors = {f: round(self._factor_pct(sid, f, raw[f]), 2) for f in FACTORS}
        worst = self.worst_step(sid, cls, bucket, st)
        out = {
            "p": round(pct, 2), "state": state, "factors": factors,
            "why": self.explain(factors) if state == "unusual" else "",
            "cell": int(worst["cell"]),
            "worst": [round(float(worst["x"]), 4), round(float(worst["y"]), 4)],
        }
        tr["peek"] = out
        return out

    def worst_step(self, sid: int, cls: str, bucket: int, st: list[dict]) -> dict:
        """The single least-likely step, so the trail can put a tick on the exact moment rather
        than shading the whole path and leaving the operator to guess."""
        best = st[-1]
        best_ll = float("inf")
        for s in st:
            d = density_bucket(int(s.get("density", 0)))
            c = self.stats_for(sid, s["cell"], bucket, d, cls)
            ll = (c.log_heading(s["heading"]) + c.log_speed(s["speed"])
                  + c.log_trans(int(s["out"])) + c.log_dwell(float(s["dwell"])))
            if ll < best_ll:
                best_ll, best = ll, s
        return best

    def sweep(self, now: float, stale_s: float = 3.0) -> list[dict]:
        """Close and score tracks that have gone quiet. Returns the scored rows worth surfacing."""
        out: list[dict] = []
        for det_id in [k for k, v in self.live.items() if now - v.get("last", 0) > stale_s]:
            tr = self.live.pop(det_id)
            res = self.close(det_id, tr)
            if res is not None:
                out.append(res)
        if now - self._last_flush > 30.0:
            self._last_flush = now
            for sid in set(self._loaded):
                self.flush(sid)
        return out

    def close(self, det_id: str, tr: dict) -> dict | None:
        """Score a finished track, then fold it into the field."""
        sid = int(tr["source_id"])
        samples = tr["samples"]
        dur = float(samples[-1]["t"] - samples[0]["t"]) if len(samples) > 1 else 0.0
        if dur < float(self._cfg("min_track_s", 4.0)) or len(samples) < 3:
            return None
        path = resample(samples, self.dt)
        st = steps(path, self.grid)
        if not st:
            return None
        # FOG OF WAR: a track that ended inside a shadow was hidden, not lost. Drop the terminate
        # step entirely so neither the model nor the score sees a disappearance that never was.
        last = st[-1]
        if self.occluded is not None and self.occluded(sid, last["x"], last["y"]):
            st = st[:-1]
            if not st:
                return None
        cls = str(tr.get("cls", "person"))
        bucket = time_bucket(samples[0]["t"], self.buckets)
        muted = self.muted.get(sid, set())
        st = [s for s in st if s["cell"] not in muted]
        if not st:
            return None
        scored = self.score(sid, cls, bucket, st)
        self.absorb(sid, cls, bucket, st)
        row = self.persist(sid, det_id, cls, samples, path, scored)
        return row

    # -- scoring -----------------------------------------------------------------------------
    def stats_for(self, sid: int, cell: int, bucket: int, density: int, cls: str) -> CellStats:
        key = (sid, cell, bucket, density, cls)
        st = self.cells.get(key)
        if st is None:
            st = CellStats()
            self.cells[key] = st
        return st

    def score(self, sid: int, cls: str, bucket: int, st: list[dict]) -> dict:
        """Mean per-step log-likelihood, decomposed by factor.

        The decomposition is exact, not attributed after the fact: the total IS the sum of the
        four terms, which is why the WHY card can be trusted.
        """
        min_obs = float(self._cfg("min_cell_obs", 40))
        acc = {f: 0.0 for f in FACTORS}
        n = 0
        immature = 0
        for s in st:
            d = density_bucket(int(s.get("density", 0)))
            c = self.stats_for(sid, s["cell"], bucket, d, cls)
            if c.n < min_obs:
                immature += 1
            acc["heading"] += c.log_heading(s["heading"])
            acc["speed"] += c.log_speed(s["speed"])
            acc["path"] += c.log_trans(int(s["out"]))
            acc["dwell"] += c.log_dwell(float(s["dwell"]))
            n += 1
        if n == 0:
            return {"score": 0.0, "factors": {f: 50.0 for f in FACTORS}, "state": "unjudged",
                    "immature": 1.0}
        per = {f: acc[f] / n for f in FACTORS}
        total = sum(per.values())
        return {"score": total, "per_factor": per, "immature": immature / n, "steps": n}

    # -- in-memory distributions -------------------------------------------------------------
    def _sample(self, sid: int) -> list[float]:
        """Sorted snapshot of recent scores, re-sorted only every RESORT_EVERY appends."""
        n = self._since_sort.get(sid, 0)
        if sid not in self._sorted or n >= RESORT_EVERY:
            self._sorted[sid] = sorted(self._scores.get(sid, ()))
            self._sorted_raw[sid] = {f: sorted(self._raw.get(sid, {}).get(f, ()))
                                     for f in FACTORS}
            self._since_sort[sid] = 0
        return self._sorted[sid]

    def remember(self, sid: int, score: float, raw: dict) -> None:
        """Fold one finished track's score into the distribution the next one is judged against."""
        self._scores.setdefault(sid, deque(maxlen=SAMPLE)).append(float(score))
        bag = self._raw.setdefault(sid, {})
        for f in FACTORS:
            if f in raw:
                bag.setdefault(f, deque(maxlen=SAMPLE)).append(float(raw[f]))
        self._since_sort[sid] = self._since_sort.get(sid, 0) + 1

    def percentile(self, sid: int, score: float) -> float:
        """Where this score sits in the site's own distribution of scores.

        A raw log-likelihood is meaningless across sites; a percentile is comparable everywhere
        and makes the sensitivity control mean the same thing on every camera. Read from the
        in-memory sample, never from SQL: this runs on every peek, per subject.
        """
        arr = self._sample(sid)
        if len(arr) < 20:
            return 50.0
        return float(100.0 * bisect.bisect_left(arr, float(score)) / len(arr))

    def mature(self, sid: int) -> bool:
        return self._track_counts.get(sid, 0) >= int(self._cfg("min_tracks", 2000))

    # -- absorption --------------------------------------------------------------------------
    def absorb(self, sid: int, cls: str, bucket: int, st: list[dict]) -> None:
        half_life = float(self._cfg("forget_half_life_days", 21))
        decay = 0.5 ** (1.0 / max(1.0, half_life * 400))   # ~400 tracks a day is a busy camera
        for s in st:
            d = density_bucket(int(s.get("density", 0)))
            key = (sid, s["cell"], bucket, d, cls)
            c = self.stats_for(sid, s["cell"], bucket, d, cls)
            c.decay(decay)
            c.add(s["heading"], s["speed"], int(s["out"]), float(s["dwell"]))
            self._dirty.add(key)

    # -- persistence of a scored track -------------------------------------------------------
    def persist(self, sid: int, det_id: str, cls: str, samples: list[dict], path: list[dict],
                scored: dict) -> dict:
        thr = float(self._cfg("percentile_threshold", 0.5))
        pct = self.percentile(sid, scored["score"])
        if not self.mature(sid) or scored.get("immature", 1.0) > 0.5:
            state = "unjudged"
        elif pct <= thr:
            state = "unusual"
        else:
            state = "ordinary"
        raw = scored["per_factor"]
        factors = {f: round(self._factor_pct(sid, f, raw[f]), 2) for f in FACTORS}
        # An ordinary track needs no explanation, and inventing one would teach the operator to
        # discount the sentence when it matters.
        why = self.explain(factors) if state == "unusual" else ""
        shape = shape_vector(path)
        row_id = 0
        try:
            row_id = self.db.execute(
                "INSERT INTO grain_track (source_id, det_id, cls, start_ts, end_ts, percentile,"
                " score, factors, why, path, state) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (int(sid), det_id, cls, float(samples[0]["t"]), float(samples[-1]["t"]),
                 float(pct), float(scored["score"]),
                 # both are kept: `pct` is what the WHY card shows, `raw` is what the next
                 # track's percentile is computed against (a percentile of a percentile would
                 # be meaningless)
                 json.dumps({"pct": factors, "raw": {k: round(v, 4) for k, v in raw.items()}}),
                 why, shape.tobytes(), state))
            self._track_counts[sid] = self._track_counts.get(sid, 0) + 1
        except Exception:
            pass
        # Fold this track in AFTER it has been scored, so it was judged against the distribution
        # that existed before it arrived.
        self.remember(sid, float(scored["score"]), raw)
        return {
            "id": int(row_id), "det_id": det_id, "cls": cls,
            "start_ts": float(samples[0]["t"]) * 1000.0,
            "end_ts": float(samples[-1]["t"]) * 1000.0,
            "percentile": round(pct, 2), "state": state, "factors": factors, "why": why,
            "path": [[round(p["x"], 4), round(p["y"], 4)] for p in path[::2]][:64],
        }

    def _factor_pct(self, sid: int, factor: str, value: float) -> float:
        """Empirical percentile of one factor against its own history on this camera.

        Compared against the RAW log-likelihoods, never against stored percentiles: a percentile
        of a percentile drifts toward 50 and stops meaning anything. Served from the same
        in-memory sample as `percentile`, because this is called four times per track and used
        to parse 800 JSON blobs each time.
        """
        self._sample(sid)                     # refresh the snapshot if it is stale
        arr = self._sorted_raw.get(sid, {}).get(factor) or []
        if len(arr) < 20:
            return 50.0                      # too little history to place this factor honestly
        # A factor whose history has no spread carries no information here (nobody has ever
        # dwelled in this corridor, so "dwell" cannot rank anyone). Reporting 0 or 100 for it
        # would put a confident number on an empty distribution.
        if arr[-1] - arr[0] < 1e-6:
            return 50.0
        return float(100.0 * bisect.bisect_left(arr, float(value)) / len(arr))

    @staticmethod
    def explain(factors: dict) -> str:
        """A plain sentence built from the dominant factors.

        Deliberately not an LLM: the decomposition already says exactly what happened, and a
        generated sentence could say something the numbers do not support.
        """
        phrases = {
            "path": "took a route this place almost never sees",
            "speed": "moved at a speed that is rare for this spot",
            "heading": "travelled against the usual direction here",
            "dwell": "stood still far longer than anyone normally does here",
        }
        order = sorted(factors, key=lambda f: factors[f])
        if not order:
            return ""
        base = phrases.get(order[0], "moved unusually for this place")
        if len(order) > 1 and factors[order[1]] < 25:
            base += f", and {phrases.get(order[1], 'moved unusually')}"
        return base[0].upper() + base[1:] + "."

    # -- UI queries --------------------------------------------------------------------------
    def field(self, sid: int, cam: str, bucket: int | None = None, cls: str = "person") -> dict:
        """The learned field for one condition, as the flow-field renderer wants it."""
        self.load(sid)
        b = time_bucket(time.time(), self.buckets) if bucket is None else int(bucket)
        min_obs = float(self._cfg("min_cell_obs", 40))
        gw, gh = self.grid
        merged: dict[int, CellStats] = {}
        for (s, cell, bk, _d, c), st in self.cells.items():
            if s != sid or bk != b or c != cls:
                continue
            m = merged.get(cell)
            if m is None:
                m = CellStats()
                merged[cell] = m
            m.n += st.n
            m.heading += st.heading
            m.slog_sum += st.slog_sum
            m.slog_sq += st.slog_sq
            m.slog_n += st.slog_n
            m.trans += st.trans
            m.dwell_sum += st.dwell_sum
            m.dwell_n += st.dwell_n
        cells = []
        for cell, st in sorted(merged.items()):
            if st.n < 1:
                continue
            tot = float(st.heading.sum()) or 1.0
            cells.append({
                "cell": int(cell), "n": int(st.n),
                "heading": [round(float(v / tot), 4) for v in st.heading],
                "speed": [round(v, 4) for v in st.speed_hist()],
                "modal_heading": round(st.modal_heading(), 4),
                "modal_speed": round(st.modal_speed(), 5),
                "concentration": round(st.concentration(), 4),
                "mature": bool(st.n >= min_obs),
            })
        tracks = self._track_counts.get(sid, 0)
        need = max(1, int(self._cfg("min_tracks", 2000)))
        days = 0
        try:
            rows = self.db.query(
                "SELECT MIN(start_ts), MAX(end_ts) FROM grain_track WHERE source_id = ?",
                (int(sid),))
            if rows and rows[0][0]:
                days = int((rows[0][1] - rows[0][0]) / 86400) + 1
        except Exception:
            days = 0
        return {
            "cam": cam, "tracks": tracks, "days": days,
            "mature": self.mature(sid), "maturity": round(min(1.0, tracks / need), 3),
            "grid": [gw, gh], "cells": cells,
            "bucket": b, "buckets": list(BUCKET_NAMES[:self.buckets]),
        }

    def precedents(self, track_id: int, n: int = 6) -> list[dict]:
        """The closest historical trajectories by shape.

        This is the trust-builder: "the last three times someone did this it was the courier"
        is worth more to an operator than any confidence number.
        """
        try:
            rows = self.db.query(
                "SELECT source_id, path FROM grain_track WHERE id = ?", (int(track_id),))
        except Exception:
            return []
        if not rows:
            return []
        sid, blob = int(rows[0][0]), rows[0][1]
        target = np.frombuffer(blob, np.float32)
        if target.size == 0:
            return []
        try:
            cand = self.db.query(
                "SELECT id, det_id, start_ts, end_ts, percentile, state, verdict, why, path"
                " FROM grain_track WHERE source_id = ? AND id != ? ORDER BY id DESC LIMIT 4000",
                (sid, int(track_id)))
        except Exception:
            return []
        scored = []
        for row in cand:
            v = np.frombuffer(row[8], np.float32)
            if v.size != target.size:
                continue
            scored.append((float(np.linalg.norm(v - target)), row))
        scored.sort(key=lambda t: t[0])
        out = []
        for dist, row in scored[:n]:
            out.append({
                "id": int(row[0]), "det_id": row[1],
                "start_ts": float(row[2]) * 1000.0, "end_ts": float(row[3]) * 1000.0,
                "percentile": float(row[4]), "state": row[5], "verdict": row[6],
                "why": row[7], "distance": round(dist, 4),
                "path": _unshape(np.frombuffer(row[8], np.float32)),
            })
        return out

    def ledger(self, sid: int, limit: int = 100, unusual_only: bool = False) -> list[dict]:
        q = ("SELECT id, det_id, cls, start_ts, end_ts, percentile, state, factors, why, verdict,"
             " path FROM grain_track WHERE source_id = ?")
        params: list[Any] = [int(sid)]
        if unusual_only:
            q += " AND state = 'unusual'"
        q += " ORDER BY start_ts DESC LIMIT ?"
        params.append(int(limit))
        try:
            rows = self.db.query(q, params)
        except Exception:
            return []
        out = []
        for r in rows:
            try:
                blob = json.loads(r[7])
                factors = blob.get("pct", blob) if isinstance(blob, dict) else {}
            except Exception:
                factors = {}
            out.append({
                "id": int(r[0]), "det_id": r[1], "cls": r[2],
                "start_ts": float(r[3]) * 1000.0, "end_ts": float(r[4]) * 1000.0,
                "percentile": float(r[5]), "state": r[6], "factors": factors,
                "why": r[8], "verdict": r[9],
                "path": _unshape(np.frombuffer(r[10], np.float32)),
            })
        return out

    def verdict(self, track_id: int, verdict: str | None) -> dict:
        self.db.execute("UPDATE grain_track SET verdict = ?, verdict_ts = ? WHERE id = ?",
                        (verdict, time.time(), int(track_id)))
        return {"ok": True}

    def mute(self, sid: int, cells: Iterable[int], on: bool = True) -> list[int]:
        """Paint cells in or out of scoring, and persist it.

        Sets rather than toggles: a caller that sends the same list twice (a retry, an operator
        command, a re-render) used to silently un-mute. Unmuting is an explicit `on=False`.
        """
        s = self.muted.setdefault(int(sid), set())
        now = time.time()
        for c in cells:
            c = int(c)
            if on:
                s.add(c)
                try:
                    self.db.execute(
                        "INSERT INTO grain_mute (source_id, cell, created_at) VALUES (?,?,?)"
                        " ON CONFLICT(source_id, cell) DO NOTHING", (int(sid), c, now))
                except Exception:
                    pass
            else:
                s.discard(c)
                try:
                    self.db.execute("DELETE FROM grain_mute WHERE source_id = ? AND cell = ?",
                                    (int(sid), c))
                except Exception:
                    pass
        return sorted(s)


def _unshape(v: np.ndarray) -> list[list[float]]:
    """Shape vector back to a drawable polyline (normalized to its own box)."""
    if v.size < 4 or v.size % 2:
        return []
    n = v.size // 2
    xs, ys = v[:n], v[n:]
    lo_x, hi_x = float(xs.min()), float(xs.max())
    lo_y, hi_y = float(ys.min()), float(ys.max())
    sx = (hi_x - lo_x) or 1.0
    sy = (hi_y - lo_y) or 1.0
    return [[round(float((x - lo_x) / sx), 4), round(float((y - lo_y) / sy), 4)]
            for x, y in zip(xs, ys)]
