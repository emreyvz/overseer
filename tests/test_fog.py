from pathlib import Path

import cv2
import numpy as np
import pytest

from analyzers.fog import FogAnalyzer
from camera.frame_buffer import Frame
from core.config import Config, load_config
from plugins.analyzer import EnvironmentContext
from vision.metrics import compute_metrics


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "analyzers:\n  fog:\n    enabled: true\n    contrast_ref: 50.0\n"
        "    edge_ref: 0.05\n    sharpness_ref: 300.0\n    onset: 0.6\n    clear: 0.3\n",
        encoding="utf-8",
    )
    return load_config(p)


def sharp_image() -> np.ndarray:
    tile = np.array([[0, 255], [255, 0]], dtype=np.uint8)
    board = np.tile(tile, (60, 80))
    return cv2.cvtColor(board, cv2.COLOR_GRAY2BGR)


def foggy_image() -> np.ndarray:
    rng = np.random.default_rng(3)
    base = np.full((120, 160, 3), 128, dtype=np.uint8)
    noise = rng.integers(-4, 4, base.shape, dtype=np.int16)
    return np.clip(base.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def run(analyzer: FogAnalyzer, image: np.ndarray, n: int = 12) -> float:
    prob = 0.0
    for i in range(n):
        frame = Frame(image=image, timestamp=float(i), seq=i)
        ctx = EnvironmentContext(metrics=compute_metrics(image))
        prob = analyzer.analyze(frame, ctx).values["fog_probability"]
    return prob


def test_sharp_scene_low_fog(config: Config) -> None:
    prob = run(FogAnalyzer(config), sharp_image())
    assert prob < 0.4


def test_foggy_scene_high_fog(config: Config) -> None:
    prob = run(FogAnalyzer(config), foggy_image())
    assert prob > 0.6


def test_visibility_complements_fog(config: Config) -> None:
    a = FogAnalyzer(config)
    image = foggy_image()
    reading = None
    for i in range(12):
        reading = a.analyze(Frame(image=image, timestamp=float(i), seq=i),
                            EnvironmentContext(metrics=compute_metrics(image)))
    v = reading.values["visibility"]
    p = reading.values["fog_probability"]
    assert abs((v + p) - 1.0) < 1e-6


def test_fog_onset_event(config: Config) -> None:
    a = FogAnalyzer(config)
    image = foggy_image()
    event = None
    for i in range(15):
        r = a.analyze(Frame(image=image, timestamp=float(i), seq=i),
                      EnvironmentContext(metrics=compute_metrics(image)))
        if r.event is not None:
            event = r.event
            break
    assert event is not None and event.label == "Fog started"


def test_fog_clear_event(config: Config) -> None:
    a = FogAnalyzer(config)
    foggy = foggy_image()
    for i in range(15):
        a.analyze(Frame(image=foggy, timestamp=float(i), seq=i),
                  EnvironmentContext(metrics=compute_metrics(foggy)))
    sharp = sharp_image()
    event = None
    for i in range(20):
        r = a.analyze(Frame(image=sharp, timestamp=float(100 + i), seq=100 + i),
                      EnvironmentContext(metrics=compute_metrics(sharp)))
        if r.event is not None:
            event = r.event
            break
    assert event is not None and event.label == "Fog cleared"
