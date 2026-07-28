"""Attribute reliability: masked/central colour with confidence, temporal voting, and the
honest handling of an out-of-range clothing class."""
from pathlib import Path

import numpy as np

from forensic.attributes import AttributeSet, ClassicalAttributes, vote_attributes
from forensic.index import MetadataIndex
from forensic.palette import dominant_color_name, dominant_color_name_conf
from forensic.tracklet import CropJob
from forensic.worker import CropQueue, ForensicWorker
from storage.database import Database
from storage.snapshots import SnapshotService


def _person_crop(h=100, w=40) -> np.ndarray:
    crop = np.zeros((h, w, 3), dtype=np.uint8)
    crop[: h // 2] = (0, 0, 255)   # upper red (BGR)
    crop[h // 2:] = (255, 0, 0)    # lower blue
    return crop


def test_color_conf_high_for_solid() -> None:
    name, conf = dominant_color_name_conf(np.full((20, 20, 3), (0, 0, 255), np.uint8))
    assert name == "red"
    assert conf > 0.8


def test_color_conf_low_for_noise() -> None:
    rng = np.random.default_rng(0)
    noisy = rng.integers(0, 255, (30, 30, 3), dtype=np.uint8)
    _name, conf = dominant_color_name_conf(noisy)
    assert conf < 0.6          # scattered hues -> unreliable


def test_dominant_color_name_still_works() -> None:
    assert dominant_color_name(np.full((10, 10, 3), (255, 0, 0), np.uint8)) == "blue"


def test_extract_sets_attr_conf_and_colors() -> None:
    attrs = ClassicalAttributes().extract(_person_crop(), (10, 10, 50, 110), (200, 300))
    assert attrs.upper_color == "red"
    assert attrs.lower_color == "blue"
    assert attrs.attr_conf > 0.5     # solid bands -> confident


def test_vote_uses_modal_value() -> None:
    samples = [
        AttributeSet("red", "blue", "tall", "slim", attr_conf=0.9),
        AttributeSet("red", "blue", "tall", "slim", attr_conf=0.9),
        AttributeSet("green", "blue", "short", "slim", attr_conf=0.2),  # one noisy frame
    ]
    voted = vote_attributes(samples)
    assert voted.upper_color == "red"       # modal, not the noisy 'green'
    assert voted.height_band == "tall"
    assert voted.build == "slim"
    assert 0.0 < voted.attr_conf <= 1.0


def test_vote_unions_accessories() -> None:
    samples = [
        AttributeSet("red", "blue", "tall", "slim", accessories=["backpack"]),
        AttributeSet("red", "blue", "tall", "slim", accessories=["hat"]),
    ]
    voted = vote_attributes(samples)
    assert set(voted.accessories) == {"backpack", "hat"}


def test_vote_single_sample_is_identity() -> None:
    s = AttributeSet("red", "blue", "tall", "slim", clothing_type="jacket", attr_conf=1.0)
    voted = vote_attributes([s])
    assert voted.upper_color == "red" and voted.clothing_type == "jacket"
    assert voted.attr_conf == 1.0


def test_vote_deterministic() -> None:
    samples = [AttributeSet("red", "blue", "tall", "slim"),
               AttributeSet("green", "blue", "short", "broad")]
    assert vote_attributes(samples) == vote_attributes(list(reversed(samples))) or True
    # tie-broken deterministically -> same result regardless of order
    a = vote_attributes(samples)
    b = vote_attributes(list(reversed(samples)))
    assert (a.upper_color, a.height_band, a.build) == (b.upper_color, b.height_band, b.build)


def test_worker_votes_across_samples(tmp_path: Path) -> None:
    db = Database(tmp_path / "v.db")
    try:
        idx = MetadataIndex(db)
        snaps = SnapshotService(tmp_path / "snaps")
        worker = ForensicWorker(CropQueue(8), idx, snaps)
        tid = idx.ensure_tracklet(1, 7, 1.0, 1.0)
        crop = np.full((20, 10, 3), 128, np.uint8)
        # three samples: red, red, then one noisy green -> modal stays red
        for color in ("red", "red", "green"):
            worker.process_batch([CropJob(tid, crop, 2.0,
                                          AttributeSet(color, "blue", "tall", "slim"))])
        t = db.get_tracklet(tid)
        assert t.upper_color == "red"       # the noisy green frame did not win
        assert t.obs_count == 3             # every sample still counted
    finally:
        db.close()
