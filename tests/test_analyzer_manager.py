from pathlib import Path

import numpy as np
import pytest

from camera.frame_buffer import Frame
from core.config import Config, load_config
from plugins.analyzer import (
    AnalyzerEvent, AnalyzerReading, BaseAnalyzer, EnvironmentContext,
)
from plugins.analyzer_manager import AnalyzerManager
from vision.metrics import ImageMetrics


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "analyzers:\n  a:\n    enabled: true\n  b:\n    enabled: true\n"
        "  \"off\":\n    enabled: false\n",
        encoding="utf-8",
    )
    return load_config(p)


def frame() -> Frame:
    return Frame(image=np.zeros((8, 8, 3), dtype=np.uint8), timestamp=0.0, seq=0)


def metrics() -> ImageMetrics:
    return ImageMetrics(brightness=100.0, contrast=40.0, sharpness=200.0, noise=1.0)


class AnalyzerA(BaseAnalyzer):
    name = "a"
    display_name = "A"

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        ctx.is_day = True
        return AnalyzerReading(values={"x": 1.0}, labels={"la": "L"},
                               event=AnalyzerEvent(label="A happened"))


class AnalyzerB(BaseAnalyzer):
    name = "b"
    display_name = "B"

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        # Sees A's merged output and is_day through ctx.
        seen = ctx.values.get("x", 0.0) + (1.0 if ctx.is_day else 0.0)
        return AnalyzerReading(values={"y": seen})


class OffAnalyzer(BaseAnalyzer):
    name = "off"
    display_name = "Off"

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        return AnalyzerReading(values={"z": 9.0})


class BrokenAnalyzer(BaseAnalyzer):
    name = "broken"
    display_name = "Bozuk"

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        raise RuntimeError("boom")


def test_enabled_from_config(config: Config) -> None:
    assert AnalyzerA(config).enabled is True
    assert OffAnalyzer(config).enabled is False


def test_register_duplicate(config: Config) -> None:
    mgr = AnalyzerManager()
    a = AnalyzerA(config)
    mgr.register(a)
    assert mgr.get("a") is a
    with pytest.raises(ValueError):
        mgr.register(AnalyzerA(config))


def test_sequential_accumulation(config: Config) -> None:
    mgr = AnalyzerManager()
    mgr.register(AnalyzerA(config))
    mgr.register(AnalyzerB(config))
    readings, events = mgr.analyze_frame(frame(), metrics())
    assert readings.values["x"] == 1.0
    assert readings.values["y"] == 2.0  # x(1.0) + is_day(1.0), B saw A's output
    assert readings.labels["la"] == "L"
    assert [e.label for e in events] == ["A happened"]


def test_disabled_skipped(config: Config) -> None:
    mgr = AnalyzerManager()
    mgr.register(OffAnalyzer(config))
    readings, events = mgr.analyze_frame(frame(), metrics())
    assert "z" not in readings.values
    assert events == []


def test_set_enabled(config: Config) -> None:
    mgr = AnalyzerManager()
    mgr.register(AnalyzerA(config))
    mgr.set_enabled("a", False)
    readings, events = mgr.analyze_frame(frame(), metrics())
    assert readings.values == {}


def test_broken_isolated(config: Config) -> None:
    mgr = AnalyzerManager()
    mgr.register(BrokenAnalyzer(config))
    mgr.register(AnalyzerA(config))
    readings, _ = mgr.analyze_frame(frame(), metrics())
    assert readings.values["x"] == 1.0  # A still ran after B broke
