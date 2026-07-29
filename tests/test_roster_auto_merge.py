# tests/test_roster_auto_merge.py
from pathlib import Path

import numpy as np

from server.roster import SessionRoster


def _roster(tmp_path: Path, **kw) -> SessionRoster:
    return SessionRoster(None, tmp_path, None, **kw)


def _put(r: SessionRoster, eid: str, cls: str, emb, *, obs: int = 1,
         plate: str | None = None, trail: dict | None = None, cam: str = "Cam1") -> str:
    e = {
        "id": eid, "cls": cls, "first_ts": 100.0, "last_ts": 100.0, "obs": obs,
        "snapshot": f"/snap/{eid}.jpg", "snapshot_path": None, "best_area": float(obs),
        "plate": plate, "attrs": {}, "cam": cam, "first_cam": cam, "last_shot": 0.0,
        "embedding": np.asarray(emb, np.float32),
        "trail": trail or {cam: {"first": 100.0, "last": 100.0, "count": obs, "clip": None}},
        "clip": None, "watched": False, "last_watch_ts": 0.0, "last_watch_cam": None,
    }
    r._entries[eid] = e
    return eid


NEAR = [1.0, 0.15, 0.0]     # cosine vs [1,0,0] ≈ 0.989  -> above 0.85
FAR = [0.5, 1.0, 0.0]       # cosine vs [1,0,0] ≈ 0.447  -> below 0.85
BASE = [1.0, 0.0, 0.0]


def test_auto_merges_confident_appearance_pair(tmp_path: Path) -> None:
    r = _roster(tmp_path, auto_merge_threshold=0.85)
    _put(r, "P-1", "person", BASE, obs=5)
    _put(r, "P-2", "person", NEAR, obs=2, cam="Cam2",
         trail={"Cam2": {"first": 200.0, "last": 210.0, "count": 2, "clip": None}})
    merged = r.auto_merge_pass()
    assert len(merged) == 1
    assert len(r.list()) == 1                       # folded into one identity
    keep = r.list()[0]
    assert keep["id"] == "P-1" and keep["obs"] == 7  # better-observed kept, sightings summed


def test_below_threshold_is_left_for_manual(tmp_path: Path) -> None:
    r = _roster(tmp_path, auto_merge_threshold=0.85)
    _put(r, "P-1", "person", BASE)
    _put(r, "P-2", "person", FAR, cam="Cam2")
    assert r.auto_merge_pass() == []
    assert len(r.list()) == 2


def test_not_same_is_never_auto_merged(tmp_path: Path) -> None:
    r = _roster(tmp_path, auto_merge_threshold=0.85)
    _put(r, "P-1", "person", BASE)
    _put(r, "P-2", "person", NEAR, cam="Cam2")
    r.reject_merge("P-1", "P-2")
    assert r.auto_merge_pass() == []
    assert len(r.list()) == 2


def test_same_time_different_camera_shield(tmp_path: Path) -> None:
    r = _roster(tmp_path, auto_merge_threshold=0.85)
    # look-alikes seen at the SAME time on DIFFERENT cameras -> can't be one subject
    _put(r, "P-1", "person", BASE, trail={"CamA": {"first": 100.0, "last": 110.0, "count": 1, "clip": None}})
    _put(r, "P-2", "person", NEAR, trail={"CamB": {"first": 105.0, "last": 115.0, "count": 1, "clip": None}})
    assert r.auto_merge_pass() == []
    assert len(r.list()) == 2


def test_same_plate_merges_regardless_of_appearance(tmp_path: Path) -> None:
    r = _roster(tmp_path, auto_merge_threshold=0.85)
    _put(r, "V-1", "vehicle", BASE, obs=3, plate="34ABC12")
    _put(r, "V-2", "vehicle", FAR, obs=1, plate="34ABC12", cam="Cam2",
         trail={"Cam2": {"first": 300.0, "last": 300.0, "count": 1, "clip": None}})
    merged = r.auto_merge_pass()
    assert len(merged) == 1 and len(r.list()) == 1


def test_disabled_does_nothing(tmp_path: Path) -> None:
    r = _roster(tmp_path, auto_merge=False, auto_merge_threshold=0.85)
    _put(r, "P-1", "person", BASE)
    _put(r, "P-2", "person", NEAR, cam="Cam2")
    assert r.auto_merge_pass() == []
    assert len(r.list()) == 2
