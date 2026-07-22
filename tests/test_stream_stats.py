"""Phase 22: stream quality stats (frame-gap latency + reconnect count)."""
from __future__ import annotations


def test_gap_and_reconnects() -> None:
    from camera.stream_stats import StreamStats

    s = StreamStats(alpha=0.5)
    assert s.gap_ms == 0.0 and s.reconnects == 0
    s.record_frame(1.0)                 # first frame: no gap yet
    assert s.gap_ms == 0.0
    s.record_frame(1.1)                 # ~100 ms gap
    assert 90.0 < s.gap_ms < 110.0
    s.record_frame(1.2)                 # steady 100 ms -> EMA stays ~100
    assert 90.0 < s.gap_ms < 110.0
    s.record_reconnect()
    s.record_reconnect()
    assert s.reconnects == 2
    s.reset()
    assert s.gap_ms == 0.0 and s.reconnects == 0


def test_backwards_timestamp_clamped() -> None:
    from camera.stream_stats import StreamStats

    s = StreamStats()
    s.record_frame(2.0)
    s.record_frame(1.0)                 # backwards -> gap clamped to 0
    assert s.gap_ms == 0.0
