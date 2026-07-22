from pathlib import Path

import numpy as np
import pytest

from analyzers.lightning import LightningAnalyzer
from camera.frame_buffer import Frame
from core.config import Config, load_config
from events.types import EventType
from plugins.analyzer import EnvironmentContext
from vision.metrics import ImageMetrics


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "analyzers:\n  lightning:\n    enabled: true\n    spike_delta: 40.0\n"
        "    spike_ratio: 1.5\n    refractory_seconds: 2.0\n",
        encoding="utf-8",
    )
    return load_config(p)


def _frame(ts: float) -> Frame:
    return Frame(image=np.zeros((4, 4, 3), dtype=np.uint8), timestamp=ts, seq=0)


def ctx(brightness: float) -> EnvironmentContext:
    return EnvironmentContext(
        metrics=ImageMetrics(brightness=brightness, contrast=0.0, sharpness=0.0,
                             noise=0.0)
    )


def test_steady_no_flash(config: Config) -> None:
    a = LightningAnalyzer(config)
    reading = None
    for i in range(10):
        reading = a.analyze(_frame(float(i)), ctx(50.0))
    assert reading.values["lightning_flash"] == 0.0
    assert reading.event is None


def test_spike_flashes_with_lightning_event(config: Config) -> None:
    a = LightningAnalyzer(config)
    for i in range(10):
        a.analyze(_frame(float(i)), ctx(50.0))  # settle baseline ~50
    reading = a.analyze(_frame(20.0), ctx(160.0))  # 160 > 50+40 and > 50*1.5
    assert reading.values["lightning_flash"] == 1.0
    assert reading.event is not None
    assert reading.event.label == "Lightning"
    assert reading.event.event_type is EventType.LIGHTNING


def test_refractory_suppresses_second_flash(config: Config) -> None:
    a = LightningAnalyzer(config)
    for i in range(10):
        a.analyze(_frame(float(i)), ctx(50.0))
    first = a.analyze(_frame(20.0), ctx(160.0))
    second = a.analyze(_frame(20.5), ctx(160.0))  # within 2s refractory
    assert first.event is not None
    assert second.event is None  # suppressed
    later = a.analyze(_frame(23.0), ctx(160.0))  # after refractory
    assert later.event is not None


def test_reset_clears_state(config: Config) -> None:
    a = LightningAnalyzer(config)
    for i in range(15):
        a.analyze(_frame(float(i)), ctx(10.0))  # settle a dim baseline (~10)
    a.reset()
    # After reset the next frame is treated as the first: baseline is re-seeded,
    # so a bright frame does NOT fire a spurious flash.
    reading = a.analyze(_frame(20.0), ctx(200.0))
    assert reading.values["lightning_flash"] == 0.0
    assert reading.event is None
