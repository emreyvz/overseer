"""Storage growth forecast — estimate how long free disk lasts at the current
recording/snapshot growth rate. Pure functions, no I/O."""
from __future__ import annotations

_DAY = 86400.0


def growth_rate(total_bytes: float, span_seconds: float) -> float:
    """Bytes/day accumulated over an observation span. Returns 0 when the span
    is too short (< 12h) to give a stable rate."""
    if span_seconds < _DAY / 2:
        return 0.0
    return total_bytes / (span_seconds / _DAY)


def days_until_full(free_bytes: float, rate_bytes_per_day: float) -> float | None:
    """Days until `free_bytes` is exhausted at `rate_bytes_per_day`. None when
    the rate is non-positive (no measurable growth)."""
    if rate_bytes_per_day <= 0:
        return None
    return free_bytes / rate_bytes_per_day
