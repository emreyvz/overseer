"""PNG chart export via matplotlib (Agg backend, no GUI)."""
from __future__ import annotations

import time
from collections import defaultdict
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 (must follow use("Agg"))

from storage.database import DailyStat  # noqa: E402


def daily_counts_png(stats: list[DailyStat], path: Path, title: str) -> None:
    """Export daily event counts as a PNG bar chart.

    Args:
        stats: List of daily statistics to aggregate and chart.
        path: Output path for PNG file (parent directories created if needed).
        title: Chart title.

    Raises:
        OSError: If file write fails.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    totals: dict[float, int] = defaultdict(int)
    for stat in stats:
        totals[stat.day_start] += stat.count
    days = sorted(totals)
    labels = [time.strftime("%m-%d", time.localtime(d)) for d in days]
    values = [totals[d] for d in days]

    figure, axes = plt.subplots(figsize=(8, 4))
    try:
        if values:
            axes.bar(labels, values, color="#2f6feb")
            axes.set_ylabel("Event count")
        else:
            axes.text(0.5, 0.5, "Veri yok", ha="center", va="center",
                      transform=axes.transAxes)
        axes.set_title(title)
        figure.autofmt_xdate(rotation=45)
        figure.tight_layout()
        figure.savefig(str(path), dpi=100)
    finally:
        plt.close(figure)
