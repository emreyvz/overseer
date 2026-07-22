from pathlib import Path

import numpy as np
import pytest

from analyzers.daynight import DayNightAnalyzer
from camera.frame_buffer import Frame
from core.config import Config, load_config
from events.types import EventType
from plugins.analyzer import EnvironmentContext
from vision.metrics import ImageMetrics


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "analyzers:\n  daynight:\n    enabled: true\n"
        "    night_below: 50.0\n    day_above: 70.0\n",
        encoding="utf-8",
    )
    return load_config(p)


def _frame() -> Frame:
    return Frame(image=np.zeros((4, 4, 3), dtype=np.uint8), timestamp=0.0, seq=0)


def ctx(brightness: float) -> EnvironmentContext:
    return EnvironmentContext(
        metrics=ImageMetrics(brightness=brightness, contrast=0.0, sharpness=0.0, noise=0.0)
    )


def test_bright_is_day(config: Config) -> None:
    a = DayNightAnalyzer(config)
    reading = None
    c = None
    for _ in range(10):
        c = ctx(240.0)
        reading = a.analyze(_frame(), c)
    assert c.is_day is True
    assert reading.labels["daynight"] == "Day"


def test_dark_is_night(config: Config) -> None:
    a = DayNightAnalyzer(config)
    c = None
    reading = None
    for _ in range(10):
        c = ctx(10.0)
        reading = a.analyze(_frame(), c)
    assert c.is_day is False
    assert reading.labels["daynight"] == "Night"


def test_twilight_label(config: Config) -> None:
    a = DayNightAnalyzer(config)
    reading = None
    for _ in range(10):
        reading = a.analyze(_frame(), ctx(60.0))
    assert reading.labels["daynight"] == "Twilight"


def test_sunset_event(config: Config) -> None:
    a = DayNightAnalyzer(config)
    for _ in range(10):
        a.analyze(_frame(), ctx(240.0))  # settle to day
    event = None
    for _ in range(20):
        r = a.analyze(_frame(), ctx(5.0))  # drop to night
        if r.event is not None:
            event = r.event
            break
    assert event is not None
    assert event.label == "Sun set"


def test_sunrise_event(config: Config) -> None:
    a = DayNightAnalyzer(config)
    for _ in range(10):
        a.analyze(_frame(), ctx(5.0))  # settle to night
    event = None
    for _ in range(20):
        r = a.analyze(_frame(), ctx(240.0))  # rise to day
        if r.event is not None:
            event = r.event
            break
    assert event is not None
    assert event.label == "Sun rose"


def test_sunrise_event_type(config: Config) -> None:
    a = DayNightAnalyzer(config)
    for _ in range(10):
        a.analyze(_frame(), ctx(5.0))  # settle to night
    event = None
    for _ in range(20):
        r = a.analyze(_frame(), ctx(240.0))  # rise to day
        if r.event is not None:
            event = r.event
            break
    assert event is not None
    assert event.event_type == EventType.SUNRISE


def test_sunset_event_type(config: Config) -> None:
    a = DayNightAnalyzer(config)
    for _ in range(10):
        a.analyze(_frame(), ctx(240.0))  # settle to day
    event = None
    for _ in range(20):
        r = a.analyze(_frame(), ctx(5.0))  # drop to night
        if r.event is not None:
            event = r.event
            break
    assert event is not None
    assert event.event_type == EventType.SUNSET


def test_golden_hour_label(config: Config) -> None:
    a = DayNightAnalyzer(config)
    reading = None
    for _ in range(20):
        reading = a.analyze(_frame(), ctx(65.0))  # upper twilight band
    assert reading.labels["solar_phase"] == "Golden Hour"


def test_blue_hour_label(config: Config) -> None:
    a = DayNightAnalyzer(config)
    reading = None
    for _ in range(20):
        reading = a.analyze(_frame(), ctx(55.0))  # lower twilight band
    assert reading.labels["solar_phase"] == "Blue Hour"
