from pathlib import Path
from typing import Iterator

import pytest

from core.config import Config, load_config
from events.types import Event, EventType
from export.scheduler import ReportScheduler
from storage.database import Database

_DAY = 86400.0
# Middle of a day so day_floor boundaries land on clean day starts.
_BASE = 200 * _DAY + 12 * 3600.0


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text("statistics:\n  enabled: true\n", encoding="utf-8")
    cfg = load_config(p)
    cfg.set("statistics.export_dir", str(tmp_path / "exp"))
    return cfg


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "c.db")
    yield d
    d.close()


def _seed_previous_day(db: Database) -> float:
    """Populate the day before _BASE with a couple of events/stats."""
    day_start = _BASE - (_BASE % _DAY) - _DAY
    db.add_event(Event(type=EventType.PERSON, timestamp=day_start + 10, source_id=1,
                       label="person"))
    db.add_event(Event(type=EventType.VEHICLE, timestamp=day_start + 20, source_id=1,
                       label="araba"))
    db.upsert_daily_stat(day_start, 1, "PERSON", 1)
    db.upsert_daily_stat(day_start, 1, "VEHICLE", 1)
    return day_start


def test_first_run_records_marker_and_returns_nothing(config: Config, db: Database) -> None:
    scheduler = ReportScheduler(config, db)
    assert scheduler.run(_BASE) == []
    assert db.get_setting("report_last_daily") is not None


def test_second_run_same_day_not_due(config: Config, db: Database) -> None:
    scheduler = ReportScheduler(config, db)
    scheduler.run(_BASE)
    assert scheduler.run(_BASE + 100.0) == []


def test_next_day_generates_report(config: Config, db: Database) -> None:
    _seed_previous_day(db)
    scheduler = ReportScheduler(config, db)
    scheduler.run(_BASE)
    paths = scheduler.run(_BASE + _DAY * 1.1)
    assert len(paths) == 1
    path = paths[0]
    assert path.exists()
    assert path.stat().st_size > 0
    assert path.read_bytes()[:4] == b"%PDF"
    assert path.parent == Path(str(config.get("statistics.export_dir"))) / "scheduled"


def test_after_generating_same_next_day_not_due_again(config: Config, db: Database) -> None:
    scheduler = ReportScheduler(config, db)
    scheduler.run(_BASE)
    first = scheduler.run(_BASE + _DAY * 1.1)
    assert len(first) == 1
    second = scheduler.run(_BASE + _DAY * 1.2)
    assert second == []


def test_weekly_report_after_week_boundary(config: Config, db: Database) -> None:
    day_start = _seed_previous_day(db)
    db.add_event(Event(type=EventType.PERSON, timestamp=day_start + 30, source_id=1,
                       label="person"))
    scheduler = ReportScheduler(config, db)
    assert scheduler.run(_BASE) == []
    assert db.get_setting("report_last_weekly") is not None

    paths = scheduler.run(_BASE + 8 * _DAY)
    weekly = [p for p in paths if p.name.startswith("report-weekly-")]
    assert len(weekly) == 1
    assert weekly[0].exists()
    assert weekly[0].parent == Path(str(config.get("statistics.export_dir"))) / "scheduled"

    # Same-week repeat run should not regenerate the weekly report.
    repeat = scheduler.run(_BASE + 8 * _DAY + 100.0)
    assert [p for p in repeat if p.name.startswith("report-weekly-")] == []


def test_monthly_report_after_month_boundary(config: Config, db: Database) -> None:
    _seed_previous_day(db)
    scheduler = ReportScheduler(config, db)
    assert scheduler.run(_BASE) == []
    assert db.get_setting("report_last_monthly") is not None

    paths = scheduler.run(_BASE + 40 * _DAY)
    monthly = [p for p in paths if p.name.startswith("report-monthly-")]
    assert len(monthly) == 1
    assert monthly[0].exists()
    assert monthly[0].parent == Path(str(config.get("statistics.export_dir"))) / "scheduled"
