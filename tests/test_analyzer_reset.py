"""Analyzer state must not leak across source switches (Fix 2)."""
from pathlib import Path

import numpy as np
import pytest

from analyzers.daynight import DayNightAnalyzer
from camera.frame_buffer import Frame
from core.config import Config, load_config
from plugins.analyzer import (
    AnalyzerReading, BaseAnalyzer, EnvironmentContext,
)
from plugins.analyzer_manager import AnalyzerManager
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


def _ctx(brightness: float) -> EnvironmentContext:
    return EnvironmentContext(
        metrics=ImageMetrics(brightness=brightness, contrast=0.0, sharpness=0.0, noise=0.0)
    )


def _settle_to_day(a: DayNightAnalyzer) -> None:
    for _ in range(10):
        a.analyze(_frame(), _ctx(240.0))


def test_daynight_without_reset_emits_spurious_event_across_sources(
    config: Config,
) -> None:
    # Baseline: proves the bug this fix addresses. A source that settled to
    # "day" hands its analyzer state straight to the next source. Even though
    # the new source is dark from its very first frame, the stale smoothed
    # brightness blends in and the analyzer reports a day->night *transition*
    # (a "Sun set" event) instead of recognizing the new source as already
    # dark. Without reset, that spurious transition event fires.
    a = DayNightAnalyzer(config)
    _settle_to_day(a)
    event = None
    for _ in range(20):
        r = a.analyze(_frame(), _ctx(5.0))  # frames of a "new" (already-dark) source
        if r.event is not None:
            event = r.event
            break
    assert event is not None
    assert event.label == "Sun set"


def test_daynight_reset_prevents_cross_source_event(config: Config) -> None:
    a = DayNightAnalyzer(config)
    _settle_to_day(a)
    a.reset()
    # Feed the same already-dark-source frames that provoked the spurious
    # event above; with reset(), every one of them is event-free because
    # "previous" is None again and the fresh state settles straight to Night.
    for _ in range(20):
        r = a.analyze(_frame(), _ctx(5.0))
        assert r.event is None
    assert r.labels["daynight"] == "Night"


class FakeResettableAnalyzer(BaseAnalyzer):
    name = "fake"
    display_name = "Sahte"

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.reset_called = False

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        return AnalyzerReading()

    def reset(self) -> None:
        self.reset_called = True


def test_reset_all_calls_reset_on_every_analyzer(config: Config) -> None:
    mgr = AnalyzerManager()
    a = FakeResettableAnalyzer(config)
    b = FakeResettableAnalyzer(config)
    b.name = "fake2"  # avoid duplicate-name registration
    mgr.register(a)
    mgr.register(b)
    assert a.reset_called is False
    assert b.reset_called is False
    mgr.reset_all()
    assert a.reset_called is True
    assert b.reset_called is True


class BrokenResetAnalyzer(BaseAnalyzer):
    name = "broken_reset"
    display_name = "Bozuk"

    def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
        return AnalyzerReading()

    def reset(self) -> None:
        raise RuntimeError("boom")


def test_reset_all_isolates_broken_analyzer(config: Config) -> None:
    mgr = AnalyzerManager()
    mgr.register(BrokenResetAnalyzer(config))
    fake = FakeResettableAnalyzer(config)
    mgr.register(fake)
    mgr.reset_all()  # must not raise despite the broken analyzer
    assert fake.reset_called is True
