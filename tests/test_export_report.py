from pathlib import Path

from export.report import daily_report_pdf
from storage.database import DailyStat


def stats() -> list[DailyStat]:
    day = 86400.0
    return [
        DailyStat(day_start=100 * day, source_id=1, event_type="PERSON", count=3,
                  updated_at=1.0),
        DailyStat(day_start=101 * day, source_id=1, event_type="PERSON", count=7,
                  updated_at=1.0),
    ]


def test_pdf_produced(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "report.pdf"
    daily_report_pdf(stats(), {"PERSON": 10, "VEHICLE": 4}, path,
                     period_label="Last 7 days")
    assert path.exists() and path.stat().st_size > 0
    assert path.read_bytes()[:4] == b"%PDF"


def test_pdf_empty(tmp_path: Path) -> None:
    path = tmp_path / "empty.pdf"
    daily_report_pdf([], {}, path, period_label="Last 1 day")
    assert path.exists()
    assert path.read_bytes()[:4] == b"%PDF"


def test_turkish_glyphs_use_unicode_font(tmp_path: Path) -> None:
    path = tmp_path / "turkish.pdf"
    daily_report_pdf(stats(), {"PERSON": 3}, path, period_label="Last 7 days")
    data = path.read_bytes()
    assert b"DejaVu" in data
    assert b"ZapfDingbats" not in data
