from alerts.engine import AlertEngine
from alerts.model import AlertRule
from alerts.summary import EventSummarizer
from events.types import Event, EventType


def _cam(source_id: int | None) -> str:
    return "Camera"


def _engine() -> AlertEngine:
    return AlertEngine(EventSummarizer(), _cam)


def _rule(**kw) -> AlertRule:
    base = dict(id=1, name="r", event_type="CROWDING", source_id=None, zone_id=None,
                min_count=None, min_duration_s=None, min_confidence=None,
                severity="warning", cooldown_s=0.0, enabled=True)
    base.update(kw)
    return AlertRule(**base)


def _ev(t=EventType.CROWDING, ts=0.0, source_id=1, confidence=None, **meta) -> Event:
    return Event(type=t, timestamp=ts, source_id=source_id, label=t.name,
                 confidence=confidence, metadata=meta)


def test_type_match_and_no_match() -> None:
    eng = _engine()
    eng.set_rules([_rule(event_type="CROWDING")])
    assert eng.evaluate(_ev(EventType.CROWDING, count=3)) is not None
    assert eng.evaluate(_ev(EventType.RUNNING, ts=1.0, speed=9)) is None


def test_disabled_rule_skipped() -> None:
    eng = _engine()
    eng.set_rules([_rule(enabled=False)])
    assert eng.evaluate(_ev(count=3)) is None


def test_source_and_zone_filters() -> None:
    eng = _engine()
    eng.set_rules([_rule(source_id=2)])
    assert eng.evaluate(_ev(source_id=1, count=3)) is None
    assert eng.evaluate(_ev(source_id=2, ts=1.0, count=3)) is not None
    eng.set_rules([_rule(event_type="RESTRICTED", zone_id=5)])
    assert eng.evaluate(_ev(EventType.RESTRICTED, ts=2.0, **{"zone_id": 9})) is None
    assert eng.evaluate(_ev(EventType.RESTRICTED, ts=3.0, **{"zone_id": 5})) is not None


def test_count_duration_confidence_thresholds() -> None:
    eng = _engine()
    eng.set_rules([_rule(min_count=8)])
    assert eng.evaluate(_ev(count=5)) is None
    assert eng.evaluate(_ev(ts=1.0, count=8)) is not None
    eng.set_rules([_rule(event_type="STOPPED", min_duration_s=900)])
    assert eng.evaluate(_ev(EventType.STOPPED, ts=2.0, **{"duration": 100})) is None
    assert eng.evaluate(_ev(EventType.STOPPED, ts=3.0, **{"duration": 900})) is not None
    eng.set_rules([_rule(event_type="PERSON", min_confidence=0.8)])
    assert eng.evaluate(_ev(EventType.PERSON, ts=4.0, confidence=0.5)) is None
    assert eng.evaluate(_ev(EventType.PERSON, ts=5.0, confidence=0.9)) is not None


def test_highest_severity_rule_wins() -> None:
    eng = _engine()
    eng.set_rules([
        _rule(id=1, severity="warning"),
        _rule(id=2, severity="critical"),
    ])
    alert = eng.evaluate(_ev(count=3))
    assert alert is not None and alert.severity == "critical" and alert.rule_id == 2


def test_cooldown_suppresses_then_rearms() -> None:
    eng = _engine()
    eng.set_rules([_rule(cooldown_s=60.0)])
    assert eng.evaluate(_ev(ts=0.0, count=3)) is not None
    assert eng.evaluate(_ev(ts=30.0, count=3)) is None       # within cooldown
    assert eng.evaluate(_ev(ts=61.0, count=3)) is not None   # re-armed


def test_reset_clears_cooldown() -> None:
    eng = _engine()
    eng.set_rules([_rule(cooldown_s=60.0)])
    assert eng.evaluate(_ev(ts=0.0, count=3)) is not None
    eng.reset()
    assert eng.evaluate(_ev(ts=1.0, count=3)) is not None     # cooldown was cleared


def test_alert_carries_summary_and_snapshot() -> None:
    eng = _engine()
    eng.set_rules([_rule(event_type="ABANDONED_OBJECT")])
    alert = eng.evaluate(_ev(EventType.ABANDONED_OBJECT,
                             **{"label": "bag", "snapshot_path": "a.jpg"}))
    assert alert is not None
    assert "Abandoned object" in alert.summary and alert.snapshot_path == "a.jpg"
    assert alert.metadata["label"] == "bag"


def test_set_rules_preserves_surviving_cooldown_prunes_removed() -> None:
    eng = _engine()
    eng.set_rules([_rule(id=1, cooldown_s=60.0)])
    assert eng.evaluate(_ev(ts=0.0, count=3)) is not None      # fires, records last_fired[1]=0.0
    eng.set_rules([_rule(id=1, cooldown_s=60.0)])              # id=1 survives -> cooldown preserved
    assert eng.evaluate(_ev(ts=30.0, count=3)) is None         # preserved cooldown -> suppressed
    eng.set_rules([_rule(id=2, cooldown_s=60.0)])              # id=1 removed -> its cooldown pruned
    eng.set_rules([_rule(id=1, cooldown_s=60.0)])              # id=1 re-added, no stale record
    assert eng.evaluate(_ev(ts=40.0, count=3)) is not None     # 40-0=40 < 60: fires ONLY if pruned
