from pathlib import Path

import numpy as np
import pytest

from analyzers.wind import WindAnalyzer
from camera.frame_buffer import Frame
from core.config import Config, load_config
from plugins.analyzer import EnvironmentContext
from vision.metrics import ImageMetrics


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "analyzers:\n  wind:\n    enabled: true\n    max_flow: 6.0\n    strong: 0.5\n",
        encoding="utf-8",
    )
    return load_config(p)


def ctx() -> EnvironmentContext:
    return EnvironmentContext(
        metrics=ImageMetrics(brightness=100.0, contrast=40.0, sharpness=200.0, noise=1.0)
    )


def _texture() -> np.ndarray:
    rng = np.random.default_rng(11)
    return rng.integers(0, 255, (240, 320, 3), dtype=np.uint8)


def test_identical_frames_no_wind(config: Config) -> None:
    a = WindAnalyzer(config)
    image = _texture()
    strength = 1.0
    for i in range(4):
        strength = a.analyze(Frame(image=image, timestamp=float(i), seq=i),
                             ctx()).values["wind_strength"]
    assert strength < 0.05


def test_shift_produces_wind(config: Config) -> None:
    a = WindAnalyzer(config)
    base = _texture()
    strength = 0.0
    for i in range(6):
        shifted = np.roll(base, shift=4 * i, axis=1)  # content moves right each frame
        strength = a.analyze(Frame(image=shifted, timestamp=float(i), seq=i),
                             ctx()).values["wind_strength"]
    assert strength > 0.05


def test_direction_is_compass(config: Config) -> None:
    a = WindAnalyzer(config)
    base = _texture()
    reading = None
    for i in range(6):
        shifted = np.roll(base, shift=4 * i, axis=1)
        reading = a.analyze(Frame(image=shifted, timestamp=float(i), seq=i), ctx())
    assert reading.labels["wind_direction"] in {"D", "KD", "GD", "K", "G", "KB", "GB", "B"}
    assert 0.0 <= reading.values["wind_direction_deg"] < 360.0


def test_first_frame_zero(config: Config) -> None:
    a = WindAnalyzer(config)
    reading = a.analyze(Frame(image=_texture(), timestamp=0.0, seq=0), ctx())
    assert reading.values["wind_strength"] == 0.0
    assert reading.labels["wind_direction"] == "D"


def test_compass_mapping(config: Config) -> None:
    a = WindAnalyzer(config)
    assert a._to_compass(0.0) == "D"
    assert a._to_compass(90.0) == "K"
    assert a._to_compass(180.0) == "B"
    assert a._to_compass(270.0) == "G"
