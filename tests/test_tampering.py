from pathlib import Path

import cv2
import numpy as np
import pytest

from analyzers.tampering import TamperingAnalyzer
from camera.frame_buffer import Frame
from core.config import Config, load_config
from plugins.analyzer import EnvironmentContext
from vision.metrics import compute_metrics


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "analyzers:\n  tampering:\n    enabled: true\n"
        "    defocus_sharpness: 60.0\n    defocus_clear: 120.0\n"
        "    obstruction_contrast: 8.0\n    obstruction_clear: 16.0\n"
        "    move_mad: 45.0\n    move_frames: 3\n",
        encoding="utf-8",
    )
    return load_config(p)


def block_image(block: int = 20, h: int = 240, w: int = 320,
                 low: int = 0, high: int = 255) -> np.ndarray:
    """Large-block checkerboard: sharp, high-contrast; blurring drops sharpness
    while a large chunk of contrast survives (unlike a fine-period checkerboard)."""
    img = np.zeros((h, w), dtype=np.uint8)
    for r in range(0, h, block):
        for c in range(0, w, block):
            if ((r // block) + (c // block)) % 2 == 0:
                img[r:r + block, c:c + block] = high
            else:
                img[r:r + block, c:c + block] = low
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def noise_image(seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (240, 320, 3), dtype=np.uint8).astype(np.uint8)


def analyze(a: TamperingAnalyzer, image: np.ndarray, ts: float) -> object:
    frame = Frame(image=image, timestamp=ts, seq=int(ts))
    ctx = EnvironmentContext(metrics=compute_metrics(image))
    return a.analyze(frame, ctx)


def test_sharp_high_contrast_no_event(config: Config) -> None:
    a = TamperingAnalyzer(config)
    image = noise_image()
    for i in range(5):
        reading = analyze(a, image, float(i))
        assert reading.event is None


def test_defocus_event_fires_once(config: Config) -> None:
    a = TamperingAnalyzer(config)
    base = block_image()
    blurred = cv2.GaussianBlur(base, (0, 0), 9)

    # establish a sharp baseline (also seeds the movement reference)
    first = analyze(a, base, 0.0)
    assert first.event is None

    reading = analyze(a, blurred, 1.0)
    assert reading.event is not None
    assert reading.event.event_type.name == "DEFOCUS"
    assert reading.event.label == "Focus loss"

    # hysteresis: an identical second blurred frame does not re-fire
    again = analyze(a, blurred, 2.0)
    assert again.event is None


def test_obstruction_event_fires(config: Config) -> None:
    a = TamperingAnalyzer(config)
    covered = np.full((240, 320, 3), 127, dtype=np.uint8)
    reading = analyze(a, covered, 0.0)
    assert reading.event is not None
    assert reading.event.event_type.name == "OBSTRUCTION"
    assert reading.event.label == "View obstructed"


def test_camera_moved_event_fires(config: Config) -> None:
    a = TamperingAnalyzer(config)
    base = block_image()
    inverted = block_image(low=255, high=0)

    # first frame just seeds the reference
    first = analyze(a, base, 0.0)
    assert first.event is None

    events = []
    for i in range(3):  # move_frames == 3
        events.append(analyze(a, inverted, float(i + 1)).event)

    assert events[0] is None
    assert events[1] is None
    assert events[2] is not None
    assert events[2].event_type.name == "CAMERA_MOVED"
    assert events[2].label == "Camera moved"


def test_reset_clears_state(config: Config) -> None:
    a = TamperingAnalyzer(config)
    base = block_image()
    blurred = cv2.GaussianBlur(base, (0, 0), 9)

    analyze(a, base, 0.0)
    fired = analyze(a, blurred, 1.0)
    assert fired.event is not None
    assert fired.event.event_type.name == "DEFOCUS"

    a.reset()
    assert a._blurred is False
    assert a._blocked is False
    assert a._ref is None
    assert a._move_count == 0

    # after reset, the very next blurred frame re-fires (state was cleared,
    # and it becomes the new movement reference so no interference)
    reading = analyze(a, blurred, 2.0)
    assert reading.event is not None
    assert reading.event.event_type.name == "DEFOCUS"
