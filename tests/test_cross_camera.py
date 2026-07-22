"""Phase 23: attribute-based cross-camera tracklet linking (movement path)."""
from __future__ import annotations

from pathlib import Path


def _tracklet(db, source_id: int, last_ts: float, *, upper: str, lower: str,
              build: str, height: str) -> int:
    tid = db.add_tracklet(source_id, None, last_ts, last_ts)
    db.update_tracklet_attributes(
        tid, height_band=height, build=build, upper_color=upper, lower_color=lower,
        clothing_type=None, accessories=[], attr_conf=0.9, snapshot_path=None,
        last_ts=last_ts, now=last_ts)
    return tid


def test_cross_camera_matches_other_camera(tmp_path: Path) -> None:
    from storage.database import Database

    db = Database(tmp_path / "c.db")
    src = _tracklet(db, 1, 1000.0, upper="red", lower="mavi", build="orta",
                    height="uzun")
    match = _tracklet(db, 2, 1100.0, upper="red", lower="mavi", build="orta",
                      height="uzun")
    far = _tracklet(db, 2, 9000.0, upper="red", lower="mavi", build="orta",
                    height="uzun")                       # outside the time window
    same_cam = _tracklet(db, 1, 1050.0, upper="red", lower="mavi", build="orta",
                         height="uzun")                  # same camera -> excluded
    diff = _tracklet(db, 3, 1080.0, upper="green", lower="siyah", build="ince",
                     height="short")                      # score 0 -> excluded

    out = db.cross_camera_candidates(src)
    ids = [t.id for t, _ in out]
    assert match in ids
    assert far not in ids and same_cam not in ids and diff not in ids
    assert out[0][0].id == match and out[0][1] == 4      # all 4 attributes match


def test_cross_camera_empty_for_unknown(tmp_path: Path) -> None:
    from storage.database import Database

    db = Database(tmp_path / "c.db")
    assert db.cross_camera_candidates(999) == []
