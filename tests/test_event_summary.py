# tests/test_event_summary.py
from alerts.summary import EventSummarizer, _human_duration
from events.types import Event, EventType


def _cam(source_id: int | None) -> str:
    return {1: "Lobby Camera"}.get(source_id or -1, "Camera")


def _ev(t: EventType, ts: float = 0.0, source_id: int | None = 1, **meta) -> Event:
    return Event(type=t, timestamp=ts, source_id=source_id, label=t.name, metadata=meta)


def test_human_duration() -> None:
    assert _human_duration(45) == "45 s"
    assert _human_duration(720) == "12 min"
    assert _human_duration(3900) == "1 h 5 min"
    assert _human_duration(3600) == "1 h"


def test_summarize_abandoned_includes_label_zone_duration() -> None:
    s = EventSummarizer().summarize_event(
        _ev(EventType.ABANDONED_OBJECT, zone_name="Lobby",
            **{"label": "blue backpack", "duration": 720}), _cam)
    assert "Abandoned object" in s and "blue backpack" in s
    assert "Lobby" in s and "12 min" in s and "Review advised" in s


def test_summarize_crowding_and_restricted() -> None:
    sm = EventSummarizer()
    assert "5 people" in sm.summarize_event(_ev(EventType.CROWDING, count=5), _cam)
    r = sm.summarize_event(_ev(EventType.RESTRICTED, zone_name="Depot"), _cam)
    assert "Restricted zone" in r and "Depot" in r and "Lobby Camera" in r


def test_summarize_fallback_uses_type_label() -> None:
    # FALLING has no bespoke template -> English type label + camera.
    s = EventSummarizer().summarize_event(_ev(EventType.FALLING), _cam)
    assert "Falling" in s and "Lobby Camera" in s


def test_unknown_source_falls_back_to_camera() -> None:
    s = EventSummarizer().summarize_event(_ev(EventType.CROWDING, source_id=99, count=3), _cam)
    assert "Camera" in s


def test_incident_counts_orders_and_picks_notable() -> None:
    sm = EventSummarizer()
    events = [
        _ev(EventType.CROWDING, ts=100.0, count=4),
        _ev(EventType.CROWDING, ts=101.0, count=6),
        _ev(EventType.LOITERING, ts=102.0, zone_name="Door"),
        _ev(EventType.ABANDONED_OBJECT, ts=103.0, **{"label": "handbag"}),
    ]
    out = sm.summarize_incident(events, now=110.0, window_s=900.0, source_name_fn=_cam)
    assert "4 events" in out
    assert "Crowding ×2" in out           # highest count first
    assert "Most notable: Abandoned object" in out   # highest severity hint


def test_incident_empty_window() -> None:
    out = EventSummarizer().summarize_incident([], now=10.0, window_s=600.0,
                                               source_name_fn=_cam)
    assert out == "No events in the last 10 minutes."
