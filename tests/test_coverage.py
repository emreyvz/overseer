"""FOG OF WAR geometry and accumulation.

Named test_coverage, not test_fog: tests/test_fog.py is the WEATHER fog detector. The same
collision exists in the module keys (`unseen` vs `fog`) and is deliberate on both sides.
"""
from __future__ import annotations

import math
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

from server.coverage import (
    DORI, CoverageField, GroundModel, TrackMortality, blobs_from_mask, estimate_pitch,
    mortality_rate, radiometric_quality, shadow_from_blob,
)
from storage.database import Database


def _gm(pitch_deg: float = 15.0, cam_h: float = 3.0) -> GroundModel:
    return GroundModel(60.0, 1920, 1080, cam_h, math.radians(pitch_deg))


# ── the pinhole ground model ────────────────────────────────────────────────────────────────

def test_range_and_row_round_trip() -> None:
    gm = _gm()
    for ny in (0.55, 0.7, 0.85, 0.99):
        z = gm.range_at(ny)
        assert z is not None
        assert gm.row_at(z) == pytest.approx(ny, abs=1e-6)


def test_nothing_lands_above_the_horizon() -> None:
    gm = _gm()
    assert gm.range_at(gm.horizon_ny - 0.05) is None
    assert gm.range_at(gm.horizon_ny + 0.05) is not None


def test_range_grows_toward_the_horizon() -> None:
    gm = _gm()
    near, far = gm.range_at(0.95), gm.range_at(0.4)
    assert near is not None and far is not None
    assert far > near > 0


def test_dori_thresholds_match_the_standard() -> None:
    # EN 62676-4: detect 25, observe 63, recognise 125, identify 250 px per metre.
    assert (DORI["detect"], DORI["observe"], DORI["recognise"], DORI["identify"]) == (
        25.0, 63.0, 125.0, 250.0)
    gm = _gm()
    for task, ppm in DORI.items():
        z = gm.range_for_px_per_m(ppm)
        assert gm.px_per_m(z) == pytest.approx(ppm, rel=1e-6)
        assert gm.dori_class(z * 0.999) == task or gm.dori_class(z * 0.5) != "blind"
    # a harder task is only satisfiable closer in
    assert gm.range_for_px_per_m(DORI["identify"]) < gm.range_for_px_per_m(DORI["detect"])


def test_pixels_per_metre_falls_with_range() -> None:
    gm = _gm()
    assert gm.px_per_m(5.0) > gm.px_per_m(20.0) > gm.px_per_m(50.0)


def test_cell_area_grows_with_range() -> None:
    """Coverage must be area-weighted: a far cell covers far more ground than a near one, and
    pixel-weighted coverage would quietly report the near field as the whole scene."""
    gm = _gm()
    near = gm.cell_area_m2(0.4, 0.45, 0.90, 0.95)
    far = gm.cell_area_m2(0.4, 0.45, 0.40, 0.45)
    assert far > near > 0


# ── channel 1: occlusion ────────────────────────────────────────────────────────────────────

def _standing(gm: GroundModel, z: float, height_m: float) -> tuple[float, float]:
    """(ny_top, ny_base) of an object of `height_m` standing at range `z`."""
    ny_base = gm.row_at(z)
    a_top = math.atan((gm.cam_h - height_m) / z)
    ny_top = 0.5 + math.tan(a_top - gm.pitch) / (2.0 * gm.tan_v)
    return ny_top, ny_base


def test_a_short_occluder_hides_a_crouching_target_but_not_a_standing_one() -> None:
    """The distinction the whole target-height control exists for."""
    gm = _gm()
    top, base = _standing(gm, 10.0, 1.7)
    assert shadow_from_blob(gm, 0.40, 0.45, top, base, target_h=1.7) is None
    crouch = shadow_from_blob(gm, 0.40, 0.45, top, base, target_h=0.5)
    assert crouch is not None
    assert crouch["z_near"] == pytest.approx(10.0, rel=0.02)
    assert crouch["z_far"] > crouch["z_near"]


def test_shadow_depth_matches_the_closed_form() -> None:
    gm = _gm()
    zp, ph, th = 12.0, 2.2, 0.0
    top, base = _standing(gm, zp, ph)
    sh = shadow_from_blob(gm, 0.3, 0.4, top, base, target_h=th)
    assert sh is not None
    expected = zp * (gm.cam_h - th) / (gm.cam_h - ph)
    assert sh["z_far"] == pytest.approx(expected, rel=0.02)


def test_an_occluder_taller_than_the_camera_shadows_to_the_horizon() -> None:
    gm = _gm(cam_h=3.0)
    top, base = _standing(gm, 8.0, 3.6)
    sh = shadow_from_blob(gm, 0.3, 0.4, top, base, target_h=0.0)
    assert sh is not None and sh["z_far"] is None
    assert sh["polygon"][0][1] == pytest.approx(gm.horizon_ny, abs=1e-6)


def test_shadow_never_extends_above_the_horizon() -> None:
    gm = _gm()
    for z in (6.0, 12.0, 25.0):
        top, base = _standing(gm, z, 2.0)
        sh = shadow_from_blob(gm, 0.2, 0.3, top, base, target_h=0.0)
        if sh is not None:
            assert sh["polygon"][0][1] >= gm.horizon_ny - 1e-9


def test_blobs_from_mask_finds_standing_objects() -> None:
    mask = np.zeros((120, 160), np.uint8)
    mask[40:100, 60:80] = 255          # one tall blob
    mask[0:2, 0:2] = 255               # speck, below the area floor
    boxes = blobs_from_mask(mask)
    assert len(boxes) == 1
    nx0, ny0, nx1, ny1 = boxes[0]
    assert nx0 == pytest.approx(60 / 160) and nx1 == pytest.approx(80 / 160)
    assert ny0 == pytest.approx(40 / 120) and ny1 == pytest.approx(100 / 120)


# ── channel 3: radiometric ──────────────────────────────────────────────────────────────────

def test_quality_is_low_on_a_flat_patch_and_high_on_texture() -> None:
    rng = np.random.default_rng(7)
    flat = np.full((90, 160, 3), 128, np.uint8)
    noisy = rng.integers(0, 255, (90, 160, 3), dtype=np.uint8)
    q_flat = radiometric_quality(flat, (8, 5)).mean()
    q_tex = radiometric_quality(noisy, (8, 5)).mean()
    assert q_flat < 0.15 < q_tex


def test_clipping_destroys_quality() -> None:
    blown = np.full((90, 160, 3), 255, np.uint8)
    assert radiometric_quality(blown, (8, 5)).mean() < 0.05


# ── channel 4: empirical mortality ──────────────────────────────────────────────────────────

def test_low_sample_cells_report_no_confidence() -> None:
    """Four crossings and one loss is not a 25% death trap, it is unknown."""
    rate, ok = mortality_rate(n_enter=4, n_die=1, n_born=0, min_samples=20)
    assert not ok and rate == 0.0


def test_mortality_is_a_beta_posterior() -> None:
    rate, ok = mortality_rate(n_enter=100, n_die=30, n_born=10, min_samples=20)
    assert ok
    assert rate == pytest.approx((30 + 10 + 1) / (200 + 2), rel=1e-6)


def test_deaths_at_the_frame_border_are_not_counted() -> None:
    m = TrackMortality(cells=64)
    m.observe("a", 0.01, 0.5, 3, now=0.0)      # born on the edge: not an interior birth
    m.observe("b", 0.5, 0.5, 12, now=0.0)      # born in the middle: that IS suspicious
    assert m.born.get(3, 0) == 0
    assert m.born.get(12, 0) == 1


def test_a_quiet_track_is_retired_as_a_death() -> None:
    m = TrackMortality(cells=64)
    m.observe("a", 0.5, 0.5, 9, now=0.0)
    m.sweep(now=1.0)
    assert m.die.get(9, 0) == 0                # still fresh
    m.sweep(now=10.0)
    assert m.die.get(9, 0) == 1


def test_drain_reports_only_dirty_cells_then_clears() -> None:
    m = TrackMortality(cells=64)
    m.observe("a", 0.5, 0.5, 9, now=0.0)
    assert [r[0] for r in m.drain()] == [9]
    assert m.drain() == []


# ── pitch estimation ────────────────────────────────────────────────────────────────────────

def test_pitch_falls_back_when_the_scene_is_flat() -> None:
    flat = np.full((64, 64), 0.5, np.float32)
    assert estimate_pitch(flat, 60.0, 1920, 1080, 12.0) == pytest.approx(math.radians(12.0))
    assert estimate_pitch(None, 60.0, 1920, 1080, 9.0) == pytest.approx(math.radians(9.0))


def test_pitch_is_recovered_from_a_depth_ramp() -> None:
    """A ground plane receding to a horizon a third of the way down the frame."""
    h = 90
    disp = np.zeros((h, 160), np.float32)
    horizon = h // 3
    disp[horizon:, :] = np.linspace(0.05, 1.0, h - horizon, dtype=np.float32)[:, None]
    pitch = estimate_pitch(disp, 60.0, 1920, 1080, 12.0)
    gm = GroundModel(60.0, 1920, 1080, 3.0, pitch)
    assert gm.horizon_ny == pytest.approx(horizon / h, abs=0.06)


# ── the assembled field ─────────────────────────────────────────────────────────────────────

def _field() -> CoverageField:
    db = Database(Path(tempfile.mkdtemp()) / "t.db")

    class _Cfg:
        def get(self, key: str, default=None):
            return default
    return CoverageField(db, _Cfg())


def test_build_returns_a_complete_payload() -> None:
    cf = _field()
    rng = np.random.default_rng(3)
    bgr = rng.integers(40, 200, (180, 320, 3), dtype=np.uint8)
    disp = np.zeros((90, 160), np.float32)
    disp[30:, :] = np.linspace(0.1, 1.0, 60, dtype=np.float32)[:, None]
    disp[45:70, 60:74] = 0.95                       # a standing object
    cov = cf.build(1, "CAM", bgr, disp, 60.0)
    assert 0.0 <= cov["percent"] <= 100.0
    assert cov["grid"] == list(cf.grid)
    assert len(cov["unseen"]) == cf.grid[0] * cf.grid[1]
    assert all(0.0 <= v <= 1.0 for v in cov["unseen"])
    assert {b["task"] for b in cov["bands"]} == set(DORI)
    assert cov["scale_estimated"] is True


def test_a_harder_dori_task_never_improves_coverage() -> None:
    cf = _field()
    rng = np.random.default_rng(11)
    bgr = rng.integers(40, 200, (180, 320, 3), dtype=np.uint8)
    disp = np.zeros((90, 160), np.float32)
    disp[30:, :] = np.linspace(0.1, 1.0, 60, dtype=np.float32)[:, None]
    detect = cf.build(1, "CAM", bgr, disp, 60.0, task="detect")["percent"]
    identify = cf.build(1, "CAM", bgr, disp, 60.0, task="identify")["percent"]
    assert identify <= detect


def test_observe_accumulates_and_survives_a_reload() -> None:
    cf = _field()
    dets = [{"id": "TK_1.1", "cls": "person", "bbox": [0.4, 0.4, 0.06, 0.2]}]
    cf.observe(1, dets, now=1000.0)
    cf.flush(1)
    rows = cf.db.query("SELECT n_enter FROM coverage_cells WHERE source_id = 1")
    assert rows and rows[0][0] >= 1
    reloaded = CoverageField(cf.db, cf.config)
    assert sum(reloaded._mort(1).enter.values()) >= 1


# ── regression: the honesty of what was claimed ─────────────────────────────────────────────

def _shadowed_field() -> CoverageField:
    cf = _field()
    cf._shadows[1] = [{"id": 0, "polygon": [[0.3, 0.3], [0.6, 0.3], [0.6, 0.8], [0.3, 0.8]],
                       "occluder": [0.4, 0.5, 0.08, 0.3], "z_near": 8.0, "z_far": 20.0,
                       "height_m": 1.9, "persistent": True}]
    return cf


def test_lost_in_fog_uses_the_subjects_own_speed() -> None:
    """The countdown was documented as 'derived from their own speed and the shadow's depth',
    but the speed key was read and never written, so every subject silently got the same default
    and a sprinter was given the same leash as someone dawdling."""
    cf = _shadowed_field()
    t = 1000.0
    fast = [{"id": "TK_1.fast", "cls": "person", "bbox": [0.45, 0.30, 0.05, 0.10]}]
    for i in range(6):                       # crossing quickly
        fast[0]["bbox"] = [0.45, 0.30 + i * 0.06, 0.05, 0.10]
        cf.observe(1, fast, t + i * 0.2)
        cf.check_losses(1, fast, t + i * 0.2)
    fast_rec = dict(cf._losses["TK_1.fast"])

    cf2 = _shadowed_field()
    slow = [{"id": "TK_1.slow", "cls": "person", "bbox": [0.45, 0.30, 0.05, 0.10]}]
    for i in range(6):                       # barely moving
        slow[0]["bbox"] = [0.45, 0.30 + i * 0.004, 0.05, 0.10]
        cf2.observe(1, slow, t + i * 0.2)
        cf2.check_losses(1, slow, t + i * 0.2)
    slow_rec = dict(cf2._losses["TK_1.slow"])

    fast_leash = fast_rec["expected_exit"] - fast_rec["entered"]
    slow_leash = slow_rec["expected_exit"] - slow_rec["entered"]
    assert slow_leash > fast_leash * 2, (
        f"speed is being ignored: fast {fast_leash:.1f}s vs slow {slow_leash:.1f}s")


def test_blind_spots_does_not_grow_a_row_every_time_it_is_asked() -> None:
    """blind_spots() runs on every Smart Suggestions open. It matched stored rows only by a
    loose centroid test, so a shadow that drifts (a van parked slightly differently) inserted a
    fresh row each time and the table grew without bound."""
    cf = _shadowed_field()
    for _ in range(8):
        cf.blind_spots(1)
    rows = cf.db.query("SELECT COUNT(*) FROM blind_spots WHERE source_id = 1")[0][0]
    assert rows <= 2, f"{rows} rows for one shadow"


def test_a_recurring_blind_spot_has_its_last_seen_refreshed() -> None:
    cf = _shadowed_field()
    cf.blind_spots(1)
    first = cf.db.query("SELECT id, last_seen FROM blind_spots WHERE source_id = 1")[0]
    time.sleep(0.02)
    cf.blind_spots(1)
    again = cf.db.query("SELECT id, last_seen FROM blind_spots WHERE id = ?", (first[0],))[0]
    assert again[1] > first[1], "a spot seen again must not look stale"
