"""Automatic scheduled PDF report generation: daily, weekly, and monthly."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

from core.config import Config
from export.report import daily_report_pdf
from storage.database import Database
from storage.statistics import day_floor

_DAY = 86400.0


class ReportScheduler:
    def __init__(self, config: Config, db: Database) -> None:
        self._db = db
        self._dir = Path(str(config.get("statistics.export_dir", "exports"))) / "scheduled"

    def run(self, now: float) -> list[Path]:
        paths: list[Path] = []
        paths.extend(self._run_daily(now))
        paths.extend(self._run_weekly(now))
        paths.extend(self._run_monthly(now))
        return paths

    def _run_daily(self, now: float) -> list[Path]:
        today = day_floor(now)
        last = float(self._db.get_setting("report_last_daily", "0") or "0")
        if last <= 0.0:
            # First run: nothing has happened yet, just record the marker so the
            # next day boundary triggers a report for the day that just completed.
            self._db.set_setting("report_last_daily", repr(today))
            return []
        if today > last:
            path = self.generate(today - _DAY)
            self._db.set_setting("report_last_daily", repr(today))
            return [path]
        return []

    def _run_weekly(self, now: float) -> list[Path]:
        d = datetime.fromtimestamp(now)
        wk_start_dt = (d - timedelta(days=d.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        iso = wk_start_dt.isocalendar()
        cur_key = f"{iso.year}-W{iso.week:02d}"
        last = self._db.get_setting("report_last_weekly")
        if last is None:
            self._db.set_setting("report_last_weekly", cur_key)
            return []
        if last != cur_key:
            prev_start_dt = wk_start_dt - timedelta(days=7)
            prev_iso = prev_start_dt.isocalendar()
            prev_key = f"{prev_iso.year}-W{prev_iso.week:02d}"
            path = self._generate_range(
                prev_start_dt.timestamp(), wk_start_dt.timestamp(), prev_key, "report-weekly"
            )
            self._db.set_setting("report_last_weekly", cur_key)
            return [path]
        return []

    def _run_monthly(self, now: float) -> list[Path]:
        d = datetime.fromtimestamp(now)
        mo_start_dt = d.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        cur_key = f"{mo_start_dt.year}-{mo_start_dt.month:02d}"
        last = self._db.get_setting("report_last_monthly")
        if last is None:
            self._db.set_setting("report_last_monthly", cur_key)
            return []
        if last != cur_key:
            prev_year, prev_month = (
                (mo_start_dt.year - 1, 12)
                if mo_start_dt.month == 1
                else (mo_start_dt.year, mo_start_dt.month - 1)
            )
            prev_start_dt = datetime(prev_year, prev_month, 1)
            path = self._generate_range(
                prev_start_dt.timestamp(),
                mo_start_dt.timestamp(),
                f"{prev_year}-{prev_month:02d}",
                "report-monthly",
            )
            self._db.set_setting("report_last_monthly", cur_key)
            return [path]
        return []

    def generate(self, day_start: float) -> Path:
        label = time.strftime("%Y-%m-%d", time.localtime(day_start))
        return self._generate_range(day_start, day_start + _DAY, label, "rapor")

    def _generate_range(self, start: float, end: float, label: str, prefix: str) -> Path:
        stats = self._db.daily_stats(start, end)
        counts = self._db.event_type_counts(start, end)
        path = self._dir / f"{prefix}-{label}.pdf"
        self._dir.mkdir(parents=True, exist_ok=True)
        daily_report_pdf(stats, counts, path, label)
        return path
