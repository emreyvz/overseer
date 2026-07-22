"""Per-session stream quality: frame-arrival latency (jitter) + reconnect count.

Pure/stateful helper the app feeds from the result + stream-status signals and
surfaces on the live-view status overlay. `record_frame` tracks the smoothed
gap between successive frames (a network/pipeline jitter proxy in ms)."""
from __future__ import annotations


class StreamStats:
    def __init__(self, alpha: float = 0.2) -> None:
        self._alpha = alpha
        self._last_ts: float | None = None
        self._gap_ms = 0.0
        self._reconnects = 0

    def record_frame(self, ts: float) -> None:
        if self._last_ts is not None:
            gap = max(0.0, (ts - self._last_ts) * 1000.0)
            self._gap_ms = gap if self._gap_ms == 0.0 else (
                (1 - self._alpha) * self._gap_ms + self._alpha * gap)
        self._last_ts = ts

    def record_reconnect(self) -> None:
        self._reconnects += 1

    def reset(self) -> None:
        self._last_ts = None
        self._gap_ms = 0.0
        self._reconnects = 0

    @property
    def gap_ms(self) -> float:
        return self._gap_ms

    @property
    def reconnects(self) -> int:
        return self._reconnects
