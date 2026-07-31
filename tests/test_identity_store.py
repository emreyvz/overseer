# tests/test_identity_store.py
"""Long-term identity: the same descriptor seen on different days must fold into ONE persisted
subject (cross-day recognition), a different descriptor must spawn a new one, gait fusion must
sharpen the decision, and the dossier aggregates the sighting history."""
from pathlib import Path

import numpy as np

from server.identity_store import SubjectStore
from storage.database import Database

DAY = 86400.0
T0 = 1_700_000_000.0   # fixed base ts (deterministic day boundaries)


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "t.db")


def _unit(rng, d=128) -> np.ndarray:
    v = rng.standard_normal(d).astype(np.float32)
    return v / np.linalg.norm(v)


def _near(v: np.ndarray, rng, cos: float) -> np.ndarray:
    """A unit vector at an exact cosine to v (deterministic 'same subject, slightly different crop')."""
    n = rng.standard_normal(v.shape).astype(np.float32)
    n -= (n @ v) * v
    n /= np.linalg.norm(n)
    return (cos * v + np.sqrt(1 - cos ** 2) * n).astype(np.float32)


def test_cross_day_recognition_and_dossier(tmp_path: Path) -> None:
    store = SubjectStore(_db(tmp_path), sighting_interval_s=1.0, descriptor_interval_s=1.0)
    rng = np.random.default_rng(0)
    alice = _unit(rng)

    # day 1: two sightings of Alice on CAM-A
    r1 = store.record("person", appearance=alice, now=T0 + 100, cam="CAM-A")
    r2 = store.record("person", appearance=_near(alice, rng, 0.9), now=T0 + 3600, cam="CAM-A")
    assert r1["is_new"] is True
    assert r2["is_new"] is False and r2["subject_id"] == r1["subject_id"]   # recognized within the day

    # a different person -> a new subject
    bob = _unit(rng)
    rb = store.record("person", appearance=bob, now=T0 + 200, cam="CAM-B")
    assert rb["is_new"] is True and rb["subject_id"] != r1["subject_id"]

    # Alice returns on day 2 and day 3 -> still ONE subject, and now flagged a repeat visitor
    store.record("person", appearance=_near(alice, rng, 0.88), now=T0 + DAY + 500, cam="CAM-B")
    r5 = store.record("person", appearance=_near(alice, rng, 0.86), now=T0 + 2 * DAY + 500, cam="CAM-A")
    assert r5["subject_id"] == r1["subject_id"]
    assert "repeat_visitor" in r5["flags"]

    dossier = store.dossier(r1["subject_id"])
    assert dossier["distinct_days"] == 3
    assert dossier["day_count"] == 3
    assert dossier["sighting_count"] >= 4
    cams = {c["cam"] for c in dossier["per_camera"]}
    assert cams == {"CAM-A", "CAM-B"}
    assert sum(dossier["hour_histogram"]) == len(dossier["sightings"])


def test_gait_fusion_separates_lookalikes(tmp_path: Path) -> None:
    # two people with similar appearance but different gait: fusion must keep them apart, where
    # appearance alone (0.72 cosine, just under threshold anyway) would be ambiguous.
    store = SubjectStore(_db(tmp_path), sighting_interval_s=1.0, descriptor_interval_s=1.0,
                         fused_threshold=0.70)
    rng = np.random.default_rng(3)
    app = _unit(rng, 128)
    gait_a = _unit(rng, 16)
    gait_b = _unit(rng, 16)   # different walk

    a = store.record("person", appearance=app, gait=gait_a, now=T0 + 1)
    # same appearance, SAME gait, later -> same subject
    a2 = store.record("person", appearance=_near(app, rng, 0.9), gait=_near(gait_a, rng, 0.9),
                      now=T0 + 100)
    assert a2["subject_id"] == a["subject_id"]
    # same appearance but DIFFERENT gait -> fused score drops below threshold -> new subject
    b = store.record("person", appearance=_near(app, rng, 0.9), gait=gait_b, now=T0 + 200)
    assert b["subject_id"] != a["subject_id"]


def test_descriptor_gallery_is_capped(tmp_path: Path) -> None:
    db = _db(tmp_path)
    sid = db.add_subject("person", T0, day="2023-11-14")
    for i in range(20):
        v = np.zeros(8, np.float32); v[i % 8] = 1.0
        db.add_subject_descriptor(sid, "appearance", v.tobytes(), 8, "reid", T0 + i, cap=12)
    rows = db.subject_descriptors("person", "appearance")
    assert len([r for r in rows if r[0] == sid]) == 12   # only the 12 newest kept


def test_merge_subjects_repoints_history(tmp_path: Path) -> None:
    db = _db(tmp_path)
    keep = db.add_subject("person", T0, day="2023-11-14")
    drop = db.add_subject("person", T0 + 10, day="2023-11-14")
    db.add_sighting(keep, T0, cam="A")
    db.add_sighting(drop, T0 + 10, cam="B")
    db.merge_subjects(keep, drop, T0 + 20)
    assert db.get_subject(drop) is None
    assert len(db.list_sightings(keep)) == 2
    assert db.get_subject(keep)["sighting_count"] == 2
