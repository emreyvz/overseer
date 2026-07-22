"""Phase 20: storage growth forecast."""
from __future__ import annotations

from pathlib import Path


def test_growth_rate() -> None:
    from storage.forecast import growth_rate

    assert growth_rate(999, 100.0) == 0.0                # span too short -> 0
    assert growth_rate(10_000_000_000, 2 * 86400.0) == 5_000_000_000.0  # 5 GB/day
    assert growth_rate(86400.0, 86400.0) == 86400.0      # exactly 1 day


def test_days_until_full() -> None:
    from storage.forecast import days_until_full

    assert days_until_full(100.0, 0.0) is None           # no growth
    assert days_until_full(100.0, -5.0) is None
    assert days_until_full(1000.0, 100.0) == 10.0


def test_oldest_recording_ts(tmp_path: Path) -> None:
    from storage.database import Database

    db = Database(tmp_path / "c.db")
    assert db.oldest_recording_ts() is None
    db.add_recording("clip", "a.avi", 100.0, 110.0, "event", None, 1, 1000)
    db.add_recording("clip", "b.avi", 50.0, 60.0, "event", None, 1, 2000)
    assert db.oldest_recording_ts() == 50.0
