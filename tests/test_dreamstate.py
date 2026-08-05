"""DREAMSTATE: a per-camera expectation model.

The load-bearing test here is common-mode rejection. A cloud crossing the sun lifts every cell at
once, and a model that fires on that is not a product. Everything else in this file is protecting
the three firing conditions from being quietly relaxed.
"""
from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

from server.dreamstate import (
    FEATURES, BUCKET_NAMES, CellModel, DreamEngine, RobustStat, blobs, cell_features,
    common_mode, iou, time_bucket,
)
from storage.database import Database


class _Cfg:
    def __init__(self, **kw) -> None:
        self.kw = kw

    def get(self, key, default=None):
        return self.kw.get(key, default)


def _engine(**cfg) -> DreamEngine:
    base = {"dream.min_observations": 30, "dream.rate_hz": 1000.0, "dream.persist_s": 0.0,
            "dream.grid": [24, 14]}
    base.update(cfg)
    return DreamEngine(Database(Path(tempfile.mkdtemp()) / "d.db"), _Cfg(**base))


def _scene(rng: np.random.Generator) -> np.ndarray:
    return rng.integers(60, 90, (180, 320, 3), dtype=np.uint8)


def _jitter(base: np.ndarray, rng: np.random.Generator, noise: int = 4) -> np.ndarray:
    f = base.astype(np.int16) + rng.integers(-noise, noise, base.shape)
    return np.clip(f, 0, 255).astype(np.uint8)


def _learn(e: DreamEngine, base: np.ndarray, plate: np.ndarray, rng: np.random.Generator,
           n: int = 200, t0: float | None = None) -> float:
    t = t0 if t0 is not None else time.time()
    for i in range(n):
        e.observe(1, _jitter(base, rng), plate, [], t + i * 0.01)
    return t


# ── robust statistics ───────────────────────────────────────────────────────────────────────

def test_the_median_tracks_a_steady_signal() -> None:
    s = RobustStat()
    for _ in range(500):
        s.update(10.0)
    assert s.median == pytest.approx(10.0, abs=0.5)


def test_a_single_outlier_does_not_move_the_model() -> None:
    """A running mean would be poisoned by exactly the events this model exists to find."""
    s = RobustStat()
    for _ in range(500):
        s.update(10.0)
    before = s.median
    s.update(10_000.0)
    assert abs(s.median - before) < 0.5


def test_sigma_is_silent_until_there_is_evidence() -> None:
    s = RobustStat()
    for _ in range(5):
        s.update(1.0)
    assert s.sigma(999.0) == 0.0            # five samples justify no claim at all


def test_sigma_grows_with_departure() -> None:
    rng = np.random.default_rng(1)
    s = RobustStat()
    for _ in range(600):
        s.update(float(10 + rng.normal(0, 1)))
    assert s.sigma(10.0) < s.sigma(14.0) < s.sigma(25.0)


def test_cell_model_survives_a_pack_round_trip() -> None:
    rng = np.random.default_rng(2)
    c = CellModel()
    for _ in range(100):
        c.update(rng.random(len(FEATURES)).astype(np.float32), 0.02)
    back = CellModel.unpack(c.pack())
    assert back.n == pytest.approx(c.n)
    for i in range(len(FEATURES)):
        assert back.stats[i].median == pytest.approx(c.stats[i].median, abs=1e-4)


def test_cell_sigma_takes_the_median_across_features() -> None:
    """One noisy feature must not carry the cell."""
    c = CellModel()
    rng = np.random.default_rng(3)
    for _ in range(300):
        c.update(np.array([0.5] * len(FEATURES), np.float32) + rng.normal(0, 0.01, len(FEATURES)).astype(np.float32), 0.02)
    lone = np.array([0.5] * len(FEATURES), np.float32)
    lone[0] = 40.0                          # one feature screaming
    assert c.sigma(lone) < 5.0
    allof = np.array([40.0] * len(FEATURES), np.float32)
    assert c.sigma(allof) > 20.0


# ── common-mode rejection ───────────────────────────────────────────────────────────────────

def test_a_uniform_lift_is_rejected() -> None:
    """The single most important line in the module."""
    sig = np.full((14, 24), 30.0, np.float32)
    assert float(common_mode(sig).max()) < 1.0


def test_a_local_spike_survives_common_mode() -> None:
    sig = np.full((14, 24), 30.0, np.float32)
    sig += np.random.default_rng(4).normal(0, 1.0, sig.shape).astype(np.float32)
    sig[7, 12] = 90.0
    z = common_mode(sig)
    assert z[7, 12] > 10.0
    assert float(np.median(z)) < 1.5


# ── blobs and persistence ───────────────────────────────────────────────────────────────────

def test_blobs_ignore_isolated_cells_and_report_plain_floats() -> None:
    m = np.zeros((14, 24), bool)
    m[3, 3] = True                          # a lone cell is noise
    assert blobs(m, min_cells=3) == []
    m[6:9, 10:13] = True                    # a coherent patch is a thing
    out = blobs(m, min_cells=3)
    assert len(out) == 1 and out[0]["area"] == 9
    json.dumps(out)                         # must survive the socket without a numpy encoder
    assert all(isinstance(v, float) for v in out[0]["bbox"])


def test_iou_matches_overlapping_boxes() -> None:
    a = [0.1, 0.1, 0.2, 0.2]
    assert iou(a, a) == pytest.approx(1.0)
    assert iou(a, [0.5, 0.5, 0.2, 0.2]) == 0.0
    assert 0.0 < iou(a, [0.15, 0.15, 0.2, 0.2]) < 1.0


def test_time_buckets_cover_the_day() -> None:
    assert 0 <= time_bucket(time.time()) < 6
    assert len(BUCKET_NAMES) == 6


# ── features ────────────────────────────────────────────────────────────────────────────────

def test_features_include_detector_occupancy() -> None:
    rng = np.random.default_rng(5)
    img = _scene(rng)
    feats, _grey = cell_features(img, None, None, [{"bbox": [0.4, 0.4, 0.2, 0.2]}], (24, 14))
    occ = feats[:, :, FEATURES.index("occupancy")]
    assert occ.max() == 1.0 and occ.min() == 0.0
    assert occ[7, 11] == 1.0                # inside the box
    assert occ[1, 1] == 0.0                 # outside it


def test_plate_departure_is_the_strongest_channel_when_something_appears() -> None:
    rng = np.random.default_rng(6)
    base = _scene(rng)
    plate = base.astype(np.float32)
    changed = base.copy()
    changed[60:100, 120:170] = 240
    feats, _ = cell_features(changed, None, plate, [], (24, 14))
    ch = feats[:, :, FEATURES.index("plate")]
    assert ch.max() > 100.0                 # the new object stands out against the plate
    assert float(np.median(ch)) < 5.0       # and nowhere else does


# ── the engine, end to end ──────────────────────────────────────────────────────────────────

def test_a_global_brightness_step_does_not_fire() -> None:
    """A cloud, an auto-exposure step, the lights coming on. The classic false positive."""
    e = _engine()
    rng = np.random.default_rng(7)
    base = _scene(rng)
    plate = base.astype(np.float32)
    t = _learn(e, base, plate, rng)
    brighter = np.clip(base.astype(np.int16) + 45, 0, 255).astype(np.uint8)
    assert e.observe(1, brighter, plate, [], t + 5) is None
    assert float(e._sigma[1].max()) < 5.0


def test_a_local_change_fires_with_a_blob() -> None:
    e = _engine()
    rng = np.random.default_rng(8)
    base = _scene(rng)
    plate = base.astype(np.float32)
    t = _learn(e, base, plate, rng)
    local = _jitter(base, rng)
    local[60:100, 120:170] = 240
    div = e.observe(1, local, plate, [], t + 6) or e.observe(1, local, plate, [], t + 8)
    assert div is not None
    assert div["peak_sigma"] > 5.0
    assert len(div["cells"]) >= 3
    assert len(div["blob"]) == 4
    json.dumps(div)


def test_an_unlearned_hour_never_fires() -> None:
    """Below the maturity floor the model has no business having an opinion."""
    e = _engine(**{"dream.min_observations": 100000})
    rng = np.random.default_rng(9)
    base = _scene(rng)
    plate = base.astype(np.float32)
    t = _learn(e, base, plate, rng, n=60)
    local = _jitter(base, rng)
    local[40:140, 60:260] = 250
    assert e.observe(1, local, plate, [], t + 5) is None


def test_persistence_is_required_before_firing() -> None:
    """One frame of surprise is not an event."""
    e = _engine(**{"dream.persist_s": 5.0})
    rng = np.random.default_rng(10)
    base = _scene(rng)
    plate = base.astype(np.float32)
    t = _learn(e, base, plate, rng)
    local = _jitter(base, rng)
    local[60:100, 120:170] = 240
    assert e.observe(1, local, plate, [], t + 1) is None      # armed, not fired
    assert e.observe(1, local, plate, [], t + 2) is None
    assert e.observe(1, local, plate, [], t + 9) is not None  # persisted long enough


def test_spatial_coherence_is_required() -> None:
    e = _engine(**{"dream.min_cells": 400})
    rng = np.random.default_rng(11)
    base = _scene(rng)
    plate = base.astype(np.float32)
    t = _learn(e, base, plate, rng)
    local = _jitter(base, rng)
    local[60:100, 120:170] = 240
    assert e.observe(1, local, plate, [], t + 6) is None
    assert e.observe(1, local, plate, [], t + 8) is None


def test_muted_cells_stay_silent() -> None:
    e = _engine()
    rng = np.random.default_rng(12)
    base = _scene(rng)
    plate = base.astype(np.float32)
    t = _learn(e, base, plate, rng)
    e.mute(1, list(range(24 * 14)))
    local = _jitter(base, rng)
    local[60:100, 120:170] = 240
    assert e.observe(1, local, plate, [], t + 6) is None
    assert e.observe(1, local, plate, [], t + 8) is None


def test_status_reports_maturity_per_bucket() -> None:
    e = _engine()
    rng = np.random.default_rng(13)
    base = _scene(rng)
    plate = base.astype(np.float32)
    _learn(e, base, plate, rng)
    st = e.status(1, "CAM")
    assert len(st["buckets"]) == 6
    assert st["maturity"] == 1.0            # the bucket we just trained
    assert len(st["cells"]) == 24 * 14
    assert st["tier"] == "A"
    # the OTHER buckets must still read as unlearned, not inherit this one's confidence
    assert sum(1 for b in st["buckets"] if b["maturity"] == 0.0) == 5


def test_the_field_survives_a_reload() -> None:
    e = _engine()
    rng = np.random.default_rng(14)
    base = _scene(rng)
    plate = base.astype(np.float32)
    _learn(e, base, plate, rng)
    e.flush(1)
    back = DreamEngine(e.db, e.config)
    back.load(1)
    assert any(k[0] == 1 for k in back.cells)
    assert max(m.n for k, m in back.cells.items() if k[0] == 1) > 50


def test_reset_forgets_the_camera() -> None:
    e = _engine()
    rng = np.random.default_rng(15)
    base = _scene(rng)
    plate = base.astype(np.float32)
    _learn(e, base, plate, rng)
    e.flush(1)
    e.reset(1)
    assert not any(k[0] == 1 for k in e.cells)
    assert e.db.query("SELECT COUNT(*) FROM dream_state WHERE source_id = 1")[0][0] == 0


def test_a_recorded_divergence_round_trips_with_its_verdict() -> None:
    e = _engine()
    div = {"source_id": 1, "ts": time.time() * 1000.0, "peak_sigma": 6.2, "area_sigma_s": 41.0,
           "blob": [[0.1, 0.1], [0.2, 0.1], [0.2, 0.2], [0.1, 0.2]], "cells": [1, 2, 3],
           "tier": "A"}
    rid = e.record(div, snapshot="/snapshots/x.jpg", triage="scene")
    assert rid > 0
    rows = e.divergences(1)
    assert rows and rows[0]["peak_sigma"] == pytest.approx(6.2)
    assert rows[0]["triage"] == "scene" and rows[0]["verdict"] is None
    e.verdict(rid, "expected")
    assert e.divergences(1)[0]["verdict"] == "expected"


def test_the_pulse_is_recorded_per_minute() -> None:
    e = _engine()
    rng = np.random.default_rng(16)
    base = _scene(rng)
    plate = base.astype(np.float32)
    t = time.time()
    _learn(e, base, plate, rng, t0=t)
    for i in range(4):                       # cross a minute boundary so the row is committed
        e.observe(1, _jitter(base, rng), plate, [], t + 70 + i * 30)
    assert e.pulse(1, hours=2)
