from pathlib import Path

import numpy as np

from camera.frame_buffer import Frame
from core.config import load_config
from forensic.attributes import ClassicalAttributes
from forensic.tracklet import CropJob, TrackletManager, TrackletView
from plugins.base import Detection


def _config(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "forensic:\n  sample_interval_seconds: 2.0\n  expire_seconds: 5.0\n"
        "  accessory_iou: 0.5\n",
        encoding="utf-8",
    )
    return load_config(p)


def _frame() -> Frame:
    img = np.zeros((200, 300, 3), dtype=np.uint8)
    img[:] = (0, 0, 255)
    return Frame(image=img, timestamp=0.0, seq=0)


def _person(track_id: int, bbox=(10, 10, 60, 160)) -> Detection:
    return Detection(label="person", confidence=0.9, bbox=bbox, category="person",
                     track_id=track_id)


def test_new_tracklet_emits_job_and_view(tmp_path: Path) -> None:
    ids = iter([101, 102, 103])
    mgr = TrackletManager(_config(tmp_path), lambda *a: next(ids), ClassicalAttributes())
    jobs, views = mgr.update(1, _frame(), [_person(7)], [], now=100.0)
    assert len(jobs) == 1 and isinstance(jobs[0], CropJob)
    assert jobs[0].tracklet_id == 101
    assert jobs[0].crop.flags["C_CONTIGUOUS"]
    assert len(views) == 1 and isinstance(views[0], TrackletView)
    assert views[0].tracklet_id == 101 and views[0].track_id == 7


def test_no_resample_before_interval(tmp_path: Path) -> None:
    ids = iter([201])
    mgr = TrackletManager(_config(tmp_path), lambda *a: next(ids), ClassicalAttributes())
    mgr.update(1, _frame(), [_person(7)], [], now=100.0)
    jobs, views = mgr.update(1, _frame(), [_person(7)], [], now=100.5)
    assert jobs == []           # within sample_interval
    assert len(views) == 1      # still tracked


def test_expire(tmp_path: Path) -> None:
    ids = iter([301, 302])
    mgr = TrackletManager(_config(tmp_path), lambda *a: next(ids), ClassicalAttributes())
    mgr.update(1, _frame(), [_person(7)], [], now=100.0)
    # Same track_id reappears after expire window -> new db id assigned.
    jobs, _ = mgr.update(1, _frame(), [_person(7)], [], now=106.0)
    assert jobs[0].tracklet_id == 302


def test_reset_clears(tmp_path: Path) -> None:
    ids = iter([401, 402])
    mgr = TrackletManager(_config(tmp_path), lambda *a: next(ids), ClassicalAttributes())
    mgr.update(1, _frame(), [_person(7)], [], now=100.0)
    mgr.reset()
    jobs, _ = mgr.update(1, _frame(), [_person(7)], [], now=100.1)
    assert jobs[0].tracklet_id == 402   # treated as new after reset


def test_null_track_id_skipped(tmp_path: Path) -> None:
    call_count = [0]
    def count_ensure_calls(*a):
        call_count[0] += 1
        return 999

    mgr = TrackletManager(_config(tmp_path), count_ensure_calls, ClassicalAttributes())
    # Person with track_id=None should be skipped
    person_no_id = Detection(label="person", confidence=0.9, bbox=(10, 10, 60, 160),
                             category="person", track_id=None)
    jobs, views = mgr.update(1, _frame(), [person_no_id], [], now=100.0)
    assert jobs == []
    assert views == []
    assert call_count[0] == 0  # ensure_tracklet not called


def test_out_of_frame_bbox_height_band(tmp_path: Path) -> None:
    ids = iter([501])
    mgr = TrackletManager(_config(tmp_path), lambda *a: next(ids), ClassicalAttributes())
    # Frame is 200x300, bbox extends below frame bottom (150 to 400 -> only 50 pixels visible)
    person = _person(7, bbox=(10, 150, 60, 400))
    jobs, views = mgr.update(1, _frame(), [person], [], now=100.0)
    assert len(jobs) == 1
    # Clamped bbox should be (10, 150, 60, 200) - 50 pixel height
    # height_band should reflect clamped height, not raw 250 pixels
    assert jobs[0].attributes.height_band is not None
    # Verify the crop reflects clamped height (50 pixels, not 250)
    clamped_height = jobs[0].crop.shape[0]
    assert clamped_height == 50, f"Expected clamped height 50, got {clamped_height}"
