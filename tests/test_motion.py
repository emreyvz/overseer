from pathlib import Path
from typing import Iterator

import numpy as np
import pytest

from camera.frame_buffer import Frame
from core.config import Config, load_config
from vision.motion import MotionDetector


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "detectors:\n  motion:\n    enabled: true\n    min_area: 100\n",
        encoding="utf-8",
    )
    return load_config(p)


def static_frames(count: int = 30) -> Iterator[Frame]:
    rng = np.random.default_rng(7)
    base = rng.integers(0, 255, (240, 320, 3), dtype=np.uint8)
    for i in range(count):
        yield Frame(image=base.copy(), timestamp=float(i), seq=i)


def moving_frames(count: int = 30) -> Iterator[Frame]:
    rng = np.random.default_rng(7)
    base = rng.integers(0, 200, (240, 320, 3), dtype=np.uint8)
    for i in range(count):
        img = base.copy()
        x = 10 + i * 6
        img[100:160, x:x + 40] = 255  # moving white block
        yield Frame(image=img, timestamp=float(i), seq=i)


def test_static_scene_no_motion(config: Config) -> None:
    det = MotionDetector(config)
    detections = []
    for frame in static_frames():
        detections = det.process(frame)
    assert detections == []
    assert det.motion_percent < 0.5


def test_moving_object_detected(config: Config) -> None:
    det = MotionDetector(config)
    hits = 0
    for frame in moving_frames():
        if det.process(frame):
            hits += 1
    assert hits >= 10  # stable detection after background learning


def test_detection_bbox_covers_moving_block(config: Config) -> None:
    det = MotionDetector(config)
    last: list = []
    last_x = 0
    for i, frame in enumerate(moving_frames()):
        result = det.process(frame)
        if result:
            last = result
            last_x = 10 + i * 6
    assert last, "motion was not detected"
    x1, y1, x2, y2 = last[0].bbox
    assert x1 <= last_x + 40 and x2 >= last_x  # box intersects with block
    assert last[0].category == "motion"
    assert last[0].label == "motion"


def test_motion_percent_range(config: Config) -> None:
    det = MotionDetector(config)
    for frame in moving_frames(10):
        det.process(frame)
    assert 0.0 <= det.motion_percent <= 100.0


def test_last_mask_exposed(config: Config) -> None:
    det = MotionDetector(config)
    assert det.last_mask is None
    for frame in moving_frames(10):
        det.process(frame)
    assert det.last_mask is not None
    assert det.last_mask.dtype.name == "uint8"
    assert det.last_mask.ndim == 2
