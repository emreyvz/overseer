from pathlib import Path
from typing import Iterator

import pytest

from core.config import Config, load_config
from events.types import Event, EventType
from storage.database import Database
from storage.statistics import StatisticsService, day_floor


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text("statistics:\n  enabled: true\n", encoding="utf-8")
    return load_config(p)


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "s.db")
    yield d
    d.close()


def test_day_floor() -> None:
    assert day_floor(100 * 86400.0 + 500.0) == 100 * 86400.0
    assert day_floor(100 * 86400.0) == 100 * 86400.0


def test_rollup_aggregates_completed_days(config: Config, db: Database) -> None:
    day = 86400.0
    d_old = 50 * day
    # events on day 50 (completed), "now" is day 52
    db.add_event(Event(type=EventType.PERSON, timestamp=d_old + 10, source_id=1,
                       label="person"))
    db.add_event(Event(type=EventType.PERSON, timestamp=d_old + 20, source_id=1,
                       label="person"))
    db.add_event(Event(type=EventType.VEHICLE, timestamp=d_old + 30, source_id=None,
                       label="araba"))
    now = 52 * day + 100.0
    written = StatisticsService(config, db).rollup(now)
    assert written >= 2
    stats = db.daily_stats(d_old, d_old + day)
    person = [s for s in stats if s.source_id == 1 and s.event_type == "PERSON"]
    assert person and person[0].count == 2
    veh = [s for s in stats if s.source_id == 0 and s.event_type == "VEHICLE"]
    assert veh and veh[0].count == 1


def test_rollup_idempotent(config: Config, db: Database) -> None:
    day = 86400.0
    db.add_event(Event(type=EventType.PERSON, timestamp=50 * day + 10, source_id=1,
                       label="person"))
    now = 52 * day
    svc = StatisticsService(config, db)
    first = svc.rollup(now)
    second = svc.rollup(now)
    assert first >= 1
    assert second == 0  # marker advanced; nothing new


def test_disabled(config: Config, db: Database) -> None:
    config.set("statistics.enabled", False)
    db.add_event(Event(type=EventType.PERSON, timestamp=50 * 86400.0, source_id=1,
                       label="person"))
    assert StatisticsService(config, db).rollup(52 * 86400.0) == 0
