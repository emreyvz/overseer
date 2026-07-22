from pathlib import Path

import numpy as np
import pytest

from analyzers.weather import WeatherAnalyzer
from camera.frame_buffer import Frame
from core.config import Config, load_config
from plugins.analyzer import EnvironmentContext
from vision.metrics import ImageMetrics


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "analyzers:\n  weather:\n    enabled: true\n    fog_high: 0.6\n"
        "    rain_high: 0.5\n    wind_high: 0.5\n    cloud_high: 0.5\n",
        encoding="utf-8",
    )
    return load_config(p)


def _frame(image: np.ndarray | None = None, timestamp: float = 0.0) -> Frame:
    if image is None:
        image = np.zeros((60, 80, 3), dtype=np.uint8)
    return Frame(image=image, timestamp=timestamp, seq=0)


def _ctx(is_day: bool | None = True, brightness: float = 150.0, **values: float
         ) -> EnvironmentContext:
    return EnvironmentContext(
        metrics=ImageMetrics(brightness=brightness, contrast=40.0, sharpness=200.0,
                             noise=1.0),
        values=dict(values), is_day=is_day,
    )


def _gray_sky() -> np.ndarray:
    image = np.zeros((60, 80, 3), dtype=np.uint8)
    image[:20] = (200, 200, 200)  # bright gray top third (BGR) -> low saturation
    return image


def _blue_sky() -> np.ndarray:
    image = np.zeros((60, 80, 3), dtype=np.uint8)
    image[:20] = (200, 120, 30)  # saturated blue top third (BGR)
    return image


def test_night(config: Config) -> None:
    r = WeatherAnalyzer(config).analyze(_frame(_blue_sky()), _ctx(is_day=False))
    assert r.labels["weather"] == "Night"


def test_foggy(config: Config) -> None:
    r = WeatherAnalyzer(config).analyze(_frame(_blue_sky()),
                                        _ctx(fog_probability=0.7))
    assert r.labels["weather"] == "Foggy"


def test_storm(config: Config) -> None:
    r = WeatherAnalyzer(config).analyze(
        _frame(_blue_sky()), _ctx(rain_probability=0.6, wind_strength=0.6))
    assert r.labels["weather"] == "Storm"


def test_rainy(config: Config) -> None:
    r = WeatherAnalyzer(config).analyze(
        _frame(_blue_sky()), _ctx(rain_probability=0.6, wind_strength=0.1))
    assert r.labels["weather"] == "Rainy"


def test_cloudy_from_gray_sky(config: Config) -> None:
    r = WeatherAnalyzer(config).analyze(_frame(_gray_sky()), _ctx())
    assert r.labels["weather"] == "Cloudy"


def test_sunny_from_blue_sky(config: Config) -> None:
    r = WeatherAnalyzer(config).analyze(_frame(_blue_sky()), _ctx())
    assert r.labels["weather"] == "Sunny"


@pytest.fixture()
def config_fast_dwell(tmp_path: Path) -> Config:
    p = tmp_path / "c_fast.yaml"
    p.write_text(
        "analyzers:\n  weather:\n    enabled: true\n    fog_high: 0.6\n"
        "    rain_high: 0.5\n    wind_high: 0.5\n    cloud_high: 0.5\n"
        "    min_dwell_seconds: 1.0\n",
        encoding="utf-8",
    )
    return load_config(p)


def test_weather_change_event(config_fast_dwell: Config) -> None:
    a = WeatherAnalyzer(config_fast_dwell)
    a.analyze(_frame(_blue_sky(), timestamp=0.0), _ctx())  # Sunny, no event first time

    # Feed the changed condition (fog) across frames with advancing timestamps
    # until the dwell period elapses; no event before that.
    r1 = a.analyze(_frame(_blue_sky(), timestamp=0.3), _ctx(fog_probability=0.7))
    assert r1.event is None
    assert r1.labels["weather"] == "Sunny"

    r2 = a.analyze(_frame(_blue_sky(), timestamp=0.6), _ctx(fog_probability=0.7))
    assert r2.event is None
    assert r2.labels["weather"] == "Sunny"

    # Candidate first became "Foggy" at t=0.3; dwell elapses at t=1.3.
    r3 = a.analyze(_frame(_blue_sky(), timestamp=1.4), _ctx(fog_probability=0.7))
    assert r3.event is not None
    assert r3.event.label == "Weather changed: Foggy"
    assert r3.labels["weather"] == "Foggy"

    # Once committed, further frames of the same condition fire no more events.
    r4 = a.analyze(_frame(_blue_sky(), timestamp=1.7), _ctx(fog_probability=0.7))
    assert r4.event is None
    assert r4.labels["weather"] == "Foggy"


def test_weather_no_flap_within_dwell(config: Config) -> None:
    # config's min_dwell_seconds is unset -> falls back to the 30s default.
    a = WeatherAnalyzer(config)
    first = a.analyze(_frame(_blue_sky(), timestamp=0.0), _ctx())
    committed = first.labels["weather"]

    images = [_gray_sky(), _blue_sky(), _gray_sky(), _blue_sky(), _gray_sky()]
    events = []
    for t, image in zip(range(1, 6), images):
        r = a.analyze(_frame(image, timestamp=float(t)), _ctx())
        if r.event is not None:
            events.append(r.event)
        assert r.labels["weather"] == committed

    assert events == []


def test_cloud_coverage_gray_vs_blue(config: Config) -> None:
    a = WeatherAnalyzer(config)
    gray = a.analyze(_frame(_gray_sky()), _ctx()).values["cloud_coverage"]
    blue = a.analyze(_frame(_blue_sky()), _ctx()).values["cloud_coverage"]
    assert gray > blue
