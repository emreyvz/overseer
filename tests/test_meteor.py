from pathlib import Path

import cv2
import numpy as np
import pytest

from analyzers.meteor import MeteorAnalyzer
from camera.frame_buffer import Frame
from core.config import Config, load_config
from events.types import EventType
from plugins.analyzer import EnvironmentContext
from vision.metrics import ImageMetrics


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "analyzers:\n  meteor:\n    enabled: true\n    diff_threshold: 40\n"
        "    min_elongation: 3.0\n    min_area: 8\n    max_area: 400\n",
        encoding="utf-8",
    )
    return load_config(p)


def _ctx(is_day: bool | None) -> EnvironmentContext:
    return EnvironmentContext(
        metrics=ImageMetrics(brightness=10.0, contrast=0.0, sharpness=0.0, noise=0.0),
        is_day=is_day,
    )


def dark() -> np.ndarray:
    return np.zeros((240, 320, 3), dtype=np.uint8)


def streak_frame() -> np.ndarray:
    img = dark()
    cv2.line(img, (100, 100), (140, 120), (255, 255, 255), 1)  # thin diagonal streak
    return img


def blob_frame() -> np.ndarray:
    img = dark()
    cv2.circle(img, (120, 110), 8, (255, 255, 255), -1)  # round blob (not a streak)
    return img


def frame(image: np.ndarray, i: int) -> Frame:
    return Frame(image=image, timestamp=float(i), seq=i)


def test_night_streak_detected(config: Config) -> None:
    a = MeteorAnalyzer(config)
    a.analyze(frame(dark(), 0), _ctx(is_day=False))       # prev = dark
    reading = a.analyze(frame(streak_frame(), 1), _ctx(is_day=False))
    assert reading.values["meteor_detected"] == 1.0
    assert reading.event is not None
    assert reading.event.label == "Meteor"
    assert reading.event.event_type is EventType.METEOR


def test_day_skips(config: Config) -> None:
    a = MeteorAnalyzer(config)
    a.analyze(frame(dark(), 0), _ctx(is_day=True))
    reading = a.analyze(frame(streak_frame(), 1), _ctx(is_day=True))
    assert reading.values["meteor_detected"] == 0.0
    assert reading.event is None


def test_round_blob_rejected(config: Config) -> None:
    a = MeteorAnalyzer(config)
    a.analyze(frame(dark(), 0), _ctx(is_day=False))
    reading = a.analyze(frame(blob_frame(), 1), _ctx(is_day=False))
    assert reading.values["meteor_detected"] == 0.0  # low elongation


def test_persistent_streak_rejected(config: Config) -> None:
    a = MeteorAnalyzer(config)
    streak = streak_frame()
    a.analyze(frame(streak, 0), _ctx(is_day=False))       # prev already has streak
    reading = a.analyze(frame(streak, 1), _ctx(is_day=False))  # no NEW bright pixels
    assert reading.values["meteor_detected"] == 0.0


def test_first_frame_zero(config: Config) -> None:
    a = MeteorAnalyzer(config)
    reading = a.analyze(frame(streak_frame(), 0), _ctx(is_day=False))
    assert reading.values["meteor_detected"] == 0.0  # no prev yet


def test_reset_clears_prev(config: Config) -> None:
    a = MeteorAnalyzer(config)
    a.analyze(frame(dark(), 0), _ctx(is_day=False))  # prev = dark
    a.reset()
    # After reset the next frame is treated as the first (prev cleared): a scene
    # change from the new source must NOT be misread as a meteor.
    reading = a.analyze(frame(streak_frame(), 1), _ctx(is_day=False))
    assert reading.values["meteor_detected"] == 0.0
