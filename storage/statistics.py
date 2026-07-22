"""Daily statistics rollup: aggregate completed days from events into a durable table."""
from __future__ import annotations

from core.config import Config
from storage.database import Database

_DAY = 86400.0


def day_floor(ts: float) -> float:
    return ts - (ts % _DAY)


class StatisticsService:
    def __init__(self, config: Config, db: Database) -> None:
        self._enabled = bool(config.get("statistics.enabled", True))
        self._db = db

    def rollup(self, now: float) -> int:
        if not self._enabled:
            return 0
        today = day_floor(now)
        start_day = self._start_day(today)
        if start_day >= today:
            return 0
        written = 0
        day = start_day
        while day < today:
            for source_id, event_type, count in self._db.daily_rollup_counts(
                day, day + _DAY
            ):
                self._db.upsert_daily_stat(day, source_id, event_type, count)
                written += 1
            day += _DAY
        self._db.set_setting("stats_rolled_through_day", str(int(today - _DAY)))
        return written

    def _start_day(self, today: float) -> float:
        marker = self._db.get_setting("stats_rolled_through_day")
        if marker is not None:
            return float(int(marker)) + _DAY
        # First run: default to 30 days back (bounded).
        return today - 30 * _DAY
