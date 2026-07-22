"""PDF report export via reportlab."""
from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path

import matplotlib
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

from events.types import TYPE_NAMES
from storage.database import DailyStat

_FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
_FONT = "DejaVuSans"
_FONT_BOLD = "DejaVuSans-Bold"


def _register_fonts() -> None:
    # Register once; reportlab tolerates re-registration but guard anyway.
    if _FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(_FONT, str(_FONT_DIR / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont(_FONT_BOLD, str(_FONT_DIR / "DejaVuSans-Bold.ttf")))
        pdfmetrics.registerFontFamily(_FONT, normal=_FONT, bold=_FONT_BOLD)


def _table(rows: list[list[str]]) -> Table:
    table = Table(rows, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f6feb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("FONTNAME", (0, 0), (-1, -1), "DejaVuSans"),
        ("FONTNAME", (0, 0), (-1, 0), "DejaVuSans-Bold"),
    ]))
    return table


def daily_report_pdf(stats: list[DailyStat], type_counts: dict[str, int], path: Path,
                     period_label: str) -> None:
    _register_fonts()
    path.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    styles["Title"].fontName = _FONT_BOLD
    styles["Heading2"].fontName = _FONT_BOLD
    styles["Normal"].fontName = _FONT
    doc = SimpleDocTemplate(str(path), pagesize=A4)
    story: list[Flowable] = [
        Paragraph("Overseer Raporu", styles["Title"]),
        Paragraph(f"Period: {period_label}", styles["Normal"]),
        Spacer(1, 12),
        Paragraph("Type Summary", styles["Heading2"]),
    ]
    if type_counts:
        rows = [["Type", "Count"]]
        rows += [[TYPE_NAMES.get(t, t), str(c)] for t, c in sorted(type_counts.items())]
        story.append(_table(rows))
    else:
        story.append(Paragraph("Veri yok", styles["Normal"]))
    story.append(Spacer(1, 12))
    story.append(Paragraph("Daily Total", styles["Heading2"]))

    totals: dict[float, int] = defaultdict(int)
    for stat in stats:
        totals[stat.day_start] += stat.count
    if totals:
        rows = [["Day", "Toplam"]]
        for day in sorted(totals):
            label = time.strftime("%Y-%m-%d", time.localtime(day))
            rows.append([label, str(totals[day])])
        story.append(_table(rows))
    else:
        story.append(Paragraph("Veri yok", styles["Normal"]))
    doc.build(story)
