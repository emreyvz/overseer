"""GRAIN: the behavioural grain of a place.

The first test in this file is the important one. GRAIN scores MOVEMENT ONLY, and that is
enforced in code rather than promised in a comment, because a model trained on "who normally
walks here" would learn to flag people who do not look like the regulars.
"""
from __future__ import annotations

import random
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

from server.grain import (
    BUCKET_NAMES, FACTORS, CellStats, GrainEngine, assert_movement_only, density_bucket,
    resample, shape_vector, steps, time_bucket,
)
from storage.database import Database


class _Cfg:
    def __init__(self, **kw) -> None:
        self.kw = kw

    def get(self, key, default=None):
        return self.kw.get(key, default)


def _engine(**cfg) -> GrainEngine:
    base = {"grain.min_tracks": 20, "grain.min_cell_obs": 3, "grain.min_track_s": 1.0}
    base.update(cfg)
    return GrainEngine(Database(Path(tempfile.mkdtemp()) / "g.db"), _Cfg(**base))


def _train(g: GrainEngine, base: float, n: int = 250, sid: int = 1) -> None:
    """n people walking left to right along y=0.70. This is the site's grain."""
    rng = random.Random(7)
    for i in range(n):
        did = f"TK_{sid}.{i}"
        y = 0.70 + rng.uniform(-0.008, 0.008)
        t = base + i * 0.2
        for k in range(20):
            g.observe(sid, did, "person", 0.10 + k * 0.04,
                      y + rng.uniform(-0.003, 0.003), t + k * 0.5)
        g.sweep(t + 200)


# ── the bias guarantee ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", [
    "upper_color", "lower_color", "height_cm", "skin_fraction", "bodytype", "make", "plate",
    "face", "gait", "embedding", "subject_uid", "label",
])
def test_appearance_and_identity_keys_are_rejected(key: str) -> None:
    """The whole reason GRAIN is defensible: it cannot see what anyone looks like."""
    with pytest.raises(ValueError, match="movement only"):
        assert_movement_only({"t": 0.0, "x": 0.5, "y": 0.5, key: "anything"})


def test_a_clean_movement_sample_passes() -> None:
    assert_movement_only({"t": 1.0, "x": 0.5, "y": 0.5, "aspect": 0.4, "density": 2}) is None


def test_resample_rejects_a_contaminated_track() -> None:
    """The guard sits at the extractor boundary: once a feature vector exists it is too late to
    tell what went into it."""
    dirty = [{"t": 0.0, "x": 0.1, "y": 0.5, "upper_color": "red"},
             {"t": 1.0, "x": 0.2, "y": 0.5}]
    with pytest.raises(ValueError):
        resample(dirty, 0.5)


# ── trajectory features ─────────────────────────────────────────────────────────────────────

def test_resample_makes_the_sampling_uniform() -> None:
    """An unresampled trajectory encodes the analysis pass's load, not the subject's behaviour."""
    jittery = [{"t": 0.0, "x": 0.1, "y": 0.5}, {"t": 0.13, "x": 0.2, "y": 0.5},
               {"t": 0.9, "x": 0.3, "y": 0.5}, {"t": 2.0, "x": 0.4, "y": 0.5}]
    out = resample(jittery, 0.5)
    gaps = [round(out[i + 1]["t"] - out[i]["t"], 6) for i in range(len(out) - 1)]
    assert gaps and all(g == 0.5 for g in gaps)


def test_steps_reports_direction_and_dwell() -> None:
    path = [{"t": i * 0.5, "x": 0.10 + i * 0.05, "y": 0.5} for i in range(5)]
    st = steps(path, (48, 27))
    assert all(s["out"] == 0 for s in st[:-1])       # due east
    assert st[-1]["out"] == 9                        # terminate
    assert all(s["dwell"] == 0 for s in st)


def test_standing_still_accumulates_dwell() -> None:
    path = [{"t": i * 0.5, "x": 0.5, "y": 0.5} for i in range(8)]
    st = steps(path, (48, 27))
    assert st[0]["out"] == 8                          # stay
    assert st[-2]["dwell"] > st[0]["dwell"] > 0


def test_a_brisk_walker_is_not_recorded_as_standing_still() -> None:
    """A step that crosses two cells is still a step. Folding it into 'stay' would put ordinary
    walking in the same bucket as loitering."""
    path = [{"t": i * 0.5, "x": 0.10 + i * 0.09, "y": 0.5} for i in range(5)]
    st = steps(path, (48, 27))
    assert all(s["out"] != 8 for s in st[:-1])
    assert all(s["dwell"] == 0 for s in st)


def test_shape_vector_is_translation_invariant() -> None:
    """Precedent lookup must match 'walked in and stopped' wherever in frame it happened."""
    a = [{"x": 0.1 + i * 0.05, "y": 0.5} for i in range(10)]
    b = [{"x": 0.4 + i * 0.05, "y": 0.8} for i in range(10)]
    assert np.allclose(shape_vector(a), shape_vector(b), atol=1e-5)


def test_time_and_density_buckets() -> None:
    assert 0 <= time_bucket(time.time()) < 6
    assert len(BUCKET_NAMES) == 6
    assert density_bucket(0) == 0 and density_bucket(5) == 1 and density_bucket(20) == 2


# ── cell statistics ─────────────────────────────────────────────────────────────────────────

def test_a_one_way_cell_reports_high_concentration() -> None:
    c = CellStats()
    for _ in range(100):
        c.add(heading=0.0, speed=0.08, out=0, dwell=0.0)
    assert c.concentration() > 0.95
    assert c.modal_heading() == pytest.approx(0.0, abs=0.25)


def test_a_two_way_cell_reports_low_concentration() -> None:
    c = CellStats()
    for _ in range(50):
        c.add(heading=0.0, speed=0.08, out=0, dwell=0.0)
        c.add(heading=np.pi, speed=0.08, out=4, dwell=0.0)
    assert c.concentration() < 0.2


def test_cell_stats_survive_a_pack_round_trip() -> None:
    c = CellStats()
    for _ in range(20):
        c.add(0.4, 0.05, 1, 0.5)
    hd, sp, tr, dw = c.pack()
    back = CellStats.unpack(c.n, hd, sp, tr, dw)
    assert back.n == pytest.approx(c.n)
    assert back.concentration() == pytest.approx(c.concentration(), abs=1e-5)
    assert back.log_speed(0.05) == pytest.approx(c.log_speed(0.05), abs=1e-3)


def test_decay_lets_a_site_change_without_forgetting_everything() -> None:
    c = CellStats()
    for _ in range(10):
        c.add(0.0, 0.05, 0, 0.0)
    n0 = c.n
    c.decay(0.5)
    assert c.n == pytest.approx(n0 * 0.5)


# ── the learned field ───────────────────────────────────────────────────────────────────────

def test_an_ordinary_walker_scores_ordinary() -> None:
    g = _engine()
    base = time.time()
    _train(g, base)
    for k in range(20):
        g.observe(1, "TK_1.ord", "person", 0.10 + k * 0.04, 0.70, base + 60 + k * 0.5)
    row = g.sweep(base + 300)[0]
    assert row["state"] == "ordinary"
    assert row["percentile"] > 50
    assert row["why"] == ""            # nothing to explain, so nothing is invented


def test_wrong_way_and_stopping_scores_unusual_with_the_right_reason() -> None:
    g = _engine()
    base = time.time()
    _train(g, base)
    for k in range(10):
        g.observe(1, "TK_1.odd", "person", 0.90 - k * 0.04, 0.70, base + 400 + k * 0.5)
    for k in range(20):
        g.observe(1, "TK_1.odd", "person", 0.50, 0.70, base + 405 + k * 0.5)
    row = g.sweep(base + 700)[0]
    assert row["state"] == "unusual"
    assert row["percentile"] < 5
    # the decomposition must point at the route and the direction, not at speed or dwell
    assert row["factors"]["path"] < 10 and row["factors"]["heading"] < 10
    assert "route" in row["why"] or "direction" in row["why"]


def test_an_uninformative_factor_reports_the_middle_not_a_confident_extreme() -> None:
    """Nobody has ever dwelled in this corridor, so dwell cannot rank anyone. Returning 0 or 100
    would put a confident number on an empty distribution."""
    g = _engine()
    base = time.time()
    _train(g, base)
    for k in range(20):
        g.observe(1, "TK_1.ord", "person", 0.10 + k * 0.04, 0.70, base + 60 + k * 0.5)
    row = g.sweep(base + 300)[0]
    assert row["factors"]["dwell"] == pytest.approx(50.0)


def test_nothing_is_judged_before_the_model_is_mature() -> None:
    g = _engine(**{"grain.min_tracks": 5000})
    base = time.time()
    _train(g, base, n=30)
    for k in range(20):
        g.observe(1, "TK_1.new", "person", 0.9 - k * 0.04, 0.2, base + 400 + k * 0.5)
    row = g.sweep(base + 700)[0]
    assert row["state"] == "unjudged"


def test_an_unvisited_region_is_unjudged_not_anomalous() -> None:
    """The third state is the whole point: ignorance must not read as suspicion."""
    g = _engine(**{"grain.min_cell_obs": 40})
    base = time.time()
    _train(g, base)
    for k in range(20):                     # a corner of frame nobody has ever walked through
        g.observe(1, "TK_1.corner", "person", 0.05 + k * 0.002, 0.05, base + 900 + k * 0.5)
    row = g.sweep(base + 1200)[0]
    assert row["state"] == "unjudged"


def test_a_short_track_carries_too_little_evidence_to_judge() -> None:
    g = _engine(**{"grain.min_track_s": 4.0})
    base = time.time()
    g.observe(1, "TK_1.blip", "person", 0.5, 0.5, base)
    g.observe(1, "TK_1.blip", "person", 0.52, 0.5, base + 0.5)
    g.observe(1, "TK_1.blip", "person", 0.54, 0.5, base + 1.0)
    assert g.sweep(base + 60) == []


# ── FOG OF WAR coupling ─────────────────────────────────────────────────────────────────────

def test_a_track_that_ends_inside_a_shadow_is_not_treated_as_a_disappearance() -> None:
    """Without this link both features generate noise: GRAIN flags an innocent person for
    vanishing and its transition model learns a 'terminate' that never happened."""
    seen: list[tuple[float, float]] = []

    def occluded(sid: int, x: float, y: float) -> bool:
        seen.append((x, y))
        return x > 0.80                     # a shadow across the right of frame

    g = _engine()
    g.occluded = occluded
    base = time.time()
    _train(g, base)
    for k in range(20):
        g.observe(1, "TK_1.hid", "person", 0.10 + k * 0.04, 0.70, base + 500 + k * 0.5)
    row = g.sweep(base + 800)[0]
    assert seen, "the shadow test must actually be consulted"
    assert row["state"] == "ordinary"       # walking into cover is not a behaviour anomaly


# ── ledger, precedents, mute ────────────────────────────────────────────────────────────────

def test_precedents_return_the_closest_trajectories_by_shape() -> None:
    g = _engine()
    base = time.time()
    _train(g, base, n=60)
    for k in range(20):
        g.observe(1, "TK_1.q", "person", 0.10 + k * 0.04, 0.70, base + 400 + k * 0.5)
    row = g.sweep(base + 700)[0]
    prec = g.precedents(row["id"], 5)
    assert len(prec) == 5
    assert all(p["id"] != row["id"] for p in prec)
    assert prec == sorted(prec, key=lambda p: p["distance"])


def test_ledger_can_filter_to_the_unusual() -> None:
    g = _engine()
    base = time.time()
    _train(g, base)
    for k in range(10):
        g.observe(1, "TK_1.odd", "person", 0.90 - k * 0.04, 0.70, base + 400 + k * 0.5)
    for k in range(20):
        g.observe(1, "TK_1.odd", "person", 0.50, 0.70, base + 405 + k * 0.5)
    g.sweep(base + 700)
    assert len(g.ledger(1, unusual_only=True)) == 1
    assert len(g.ledger(1)) > 1


def test_muted_cells_are_excluded_from_scoring() -> None:
    g = _engine()
    base = time.time()
    _train(g, base)
    cells = {s for s in range(48 * 27)}
    g.mute(1, cells)                        # mute everything: nothing is left to score
    for k in range(20):
        g.observe(1, "TK_1.any", "person", 0.10 + k * 0.04, 0.70, base + 400 + k * 0.5)
    assert g.sweep(base + 700) == []


def test_field_reports_the_learned_current() -> None:
    g = _engine()
    base = time.time()
    _train(g, base)
    f = g.field(1, "CAM", bucket=time_bucket(base))
    mature = [c for c in f["cells"] if c["mature"]]
    assert mature, "a trained corridor must produce mature cells"
    assert f["mature"] is True and f["maturity"] == 1.0
    # a one-way corridor is a strong current pointing right (heading ~ 0)
    strongest = max(mature, key=lambda c: c["n"])
    assert strongest["concentration"] > 0.9
    assert abs(strongest["modal_heading"]) < 0.3
    assert set(f["grid"]) and len(f["buckets"]) == 6


def test_the_field_survives_a_reload_from_disk() -> None:
    g = _engine()
    base = time.time()
    _train(g, base, n=40)
    g.flush(1)
    reloaded = GrainEngine(g.db, g.config)
    reloaded.load(1)
    assert any(k[0] == 1 for k in reloaded.cells)
    assert reloaded._track_counts.get(1, 0) >= 40


def test_verdict_is_recorded() -> None:
    g = _engine()
    base = time.time()
    _train(g, base, n=30)
    for k in range(20):
        g.observe(1, "TK_1.v", "person", 0.10 + k * 0.04, 0.70, base + 400 + k * 0.5)
    row = g.sweep(base + 700)[0]
    g.verdict(row["id"], "noteworthy")
    assert g.ledger(1)[0]["verdict"] == "noteworthy"


def test_every_factor_is_reported() -> None:
    g = _engine()
    base = time.time()
    _train(g, base, n=40)
    for k in range(20):
        g.observe(1, "TK_1.f", "person", 0.10 + k * 0.04, 0.70, base + 400 + k * 0.5)
    row = g.sweep(base + 700)[0]
    assert set(row["factors"]) == set(FACTORS)
    assert all(0.0 <= v <= 100.0 for v in row["factors"].values())


# ── regression: cost must not grow with the size of the record ──────────────────────────────

class _CountingDb:
    """Wraps a Database and counts queries, so a scaling regression is caught as a COUNT rather
    than as a wall-clock threshold (which would be flaky on a loaded machine)."""

    def __init__(self, db) -> None:
        self._db = db
        self.queries = 0

    def query(self, sql, params=()):
        self.queries += 1
        return self._db.query(sql, params)

    def __getattr__(self, name):
        return getattr(self._db, name)


def test_peek_does_not_query_the_database(monkeypatch) -> None:
    """percentile() and _factor_pct() used to scan grain_track on EVERY peek, and peek runs per
    subject every couple of seconds. At 100k stored tracks that measured 4.7 seconds of CPU per
    second of video for 20 subjects: a total pipeline stall that only appears after weeks in the
    field. The live path must read its distribution from memory."""
    g = _engine()
    base = time.time()
    _train(g, base, n=60)
    counting = _CountingDb(g.db)
    g.db = counting
    for k in range(20):
        g.observe(1, "TK_1.live", "person", 0.10 + k * 0.04, 0.70, base + 400 + k * 0.5)
    counting.queries = 0
    g.peek("TK_1.live", base + 420, min_interval=0.0)
    assert counting.queries == 0, f"peek hit the database {counting.queries} times"


def test_closing_a_track_costs_a_bounded_number_of_queries() -> None:
    g = _engine()
    base = time.time()
    _train(g, base, n=80)
    counting = _CountingDb(g.db)
    g.db = counting
    for k in range(20):
        g.observe(1, "TK_1.close", "person", 0.10 + k * 0.04, 0.70, base + 900 + k * 0.5)
    counting.queries = 0
    g.sweep(base + 1200)
    # one INSERT is fine; a per-factor scan of the whole table is not
    assert counting.queries <= 2, f"close() ran {counting.queries} queries"


def test_the_score_distribution_survives_a_reload() -> None:
    """The in-memory distribution has to be rebuilt on load or percentiles reset to 50 after
    every restart, silently un-learning the calibration."""
    g = _engine()
    base = time.time()
    _train(g, base, n=60)
    g.flush(1)
    back = GrainEngine(g.db, g.config)
    back.load(1)
    assert len(back._scores.get(1, [])) >= 50
    assert 0.0 <= back.percentile(1, -8.0) <= 100.0


def test_muted_cells_survive_a_restart() -> None:
    """A mute the operator painted is a setting, not session state."""
    g = _engine()
    g.mute(1, [5, 6, 7])
    back = GrainEngine(g.db, g.config)
    back.load(1)
    assert back.muted.get(1) == {5, 6, 7}


def test_muting_is_idempotent_and_unmute_is_explicit() -> None:
    """It used to toggle, so a caller sending the same list twice silently un-muted."""
    g = _engine()
    assert g.mute(1, [4]) == [4]
    assert g.mute(1, [4]) == [4]
    assert g.mute(1, [4], on=False) == []
