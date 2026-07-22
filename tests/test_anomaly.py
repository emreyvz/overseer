from pathlib import Path

from anomaly.monitor import AnomalyEvent, AnomalyMonitor
from core.config import load_config
from events.types import EventType


def _config(tmp_path: Path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "anomaly:\n  window_seconds: 1.0\n  k: 3.0\n  warmup_windows: 3\n"
        "  min_count: 3\n  alpha: 0.3\n",
        encoding="utf-8")
    return load_config(p)


def test_steady_baseline_no_false_positive(tmp_path: Path) -> None:
    mon = AnomalyMonitor(_config(tmp_path))
    events = []
    for _ in range(10):
        for _ in range(4):
            mon.record("MOTION", now=0.0)
        events += mon.tick(now=0.0)
    assert events == []


def test_spike_flagged_after_warmup(tmp_path: Path) -> None:
    mon = AnomalyMonitor(_config(tmp_path))
    # Warm up a steady baseline of ~4/window, well past warmup_windows=3.
    for _ in range(6):
        for _ in range(4):
            mon.record("MOTION", now=0.0)
        mon.tick(now=0.0)
    # Now a big spike in one window.
    for _ in range(40):
        mon.record("MOTION", now=0.0)
    events = mon.tick(now=0.0)
    assert len(events) == 1
    ev = events[0]
    assert isinstance(ev, AnomalyEvent)
    assert ev.event_type is EventType.ANOMALY
    assert ev.metadata["type"] == "MOTION"
    assert ev.metadata["count"] == 40


def test_no_flag_before_warmup(tmp_path: Path) -> None:
    mon = AnomalyMonitor(_config(tmp_path))
    # Only 1 window observed so far (< warmup_windows=3); a spike now must
    # not flag even though it's statistically way off a near-zero baseline.
    for _ in range(4):
        mon.record("MOTION", now=0.0)
    mon.tick(now=0.0)
    for _ in range(40):
        mon.record("MOTION", now=0.0)
    events = mon.tick(now=0.0)
    assert events == []


def test_min_count_floor_prevents_flag(tmp_path: Path) -> None:
    mon = AnomalyMonitor(_config(tmp_path))
    # Warm up a near-zero baseline (1/window) past warmup.
    for _ in range(6):
        mon.record("MOTION", now=0.0)
        mon.tick(now=0.0)
    # A "spike" to 2 is still below min_count=3, so it must never flag even
    # though it may be statistically above the tiny baseline.
    mon.record("MOTION", now=0.0)
    mon.record("MOTION", now=0.0)
    events = mon.tick(now=0.0)
    assert events == []


def test_reset_clears_baseline(tmp_path: Path) -> None:
    mon = AnomalyMonitor(_config(tmp_path))
    for _ in range(6):
        for _ in range(4):
            mon.record("MOTION", now=0.0)
        mon.tick(now=0.0)
    mon.reset()
    # Post-reset, a spike is treated as the first observed window (warmup
    # not yet satisfied) so it does not flag immediately.
    for _ in range(40):
        mon.record("MOTION", now=0.0)
    events = mon.tick(now=0.0)
    assert events == []
