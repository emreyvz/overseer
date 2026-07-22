from pathlib import Path

import cv2

from export.charts import daily_counts_png
from storage.database import DailyStat


def stats() -> list[DailyStat]:
    day = 86400.0
    return [
        DailyStat(day_start=100 * day, source_id=1, event_type="PERSON", count=3,
                  updated_at=1.0),
        DailyStat(day_start=100 * day, source_id=1, event_type="VEHICLE", count=2,
                  updated_at=1.0),
        DailyStat(day_start=101 * day, source_id=1, event_type="PERSON", count=7,
                  updated_at=1.0),
    ]


def test_daily_counts_png_produces_image(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "chart.png"
    daily_counts_png(stats(), path, title="Daily Events")
    assert path.exists() and path.stat().st_size > 0
    img = cv2.imread(str(path))
    assert img is not None and img.shape[2] == 3


def test_daily_counts_png_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.png"
    daily_counts_png([], path, title="Empty")
    assert path.exists()
    assert cv2.imread(str(path)) is not None
