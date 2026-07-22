# tests/test_alerts_integration.py
from pathlib import Path

from alerts.engine import AlertEngine
from alerts.summary import EventSummarizer
from events.bus import EventBus
from events.types import Event, EventType
from storage.database import Database


def _cam(source_id):
    return "Lobi"


def test_bus_event_flows_to_engine_and_db(tmp_path: Path) -> None:
    db = Database(tmp_path / "c.db")
    db.seed_default_alert_rules(crowd_min=8, cooldown_s=60.0)
    engine = AlertEngine(EventSummarizer(), _cam)
    engine.set_rules(db.list_alert_rules())
    raised = []

    def on_event(event: Event) -> None:
        alert = engine.evaluate(event)
        if alert is not None:
            alert.id = db.add_alert(alert)
            raised.append(alert)

    bus = EventBus()
    bus.subscribe(None, on_event)

    # crowd of 5 -> below default min_count 8 -> no alert
    bus.publish(Event(type=EventType.CROWDING, timestamp=1.0, source_id=1,
                      label="crowd", metadata={"count": 5}))
    assert raised == []
    # crowd of 10 -> alert
    bus.publish(Event(type=EventType.CROWDING, timestamp=2.0, source_id=1,
                      label="crowd", metadata={"count": 10}))
    assert len(raised) == 1 and raised[0].severity == "warning"
    # abandoned object -> critical alert with NL summary
    bus.publish(Event(type=EventType.ABANDONED_OBJECT, timestamp=3.0, source_id=1,
                      label="bag", metadata={"label": "blue bag", "zone_name": "Lobi"}))
    stored = db.list_alerts()
    assert len(stored) == 2
    crit = [a for a in stored if a.severity == "critical"]
    assert crit and "blue bag" in crit[0].summary


def test_cooldown_holds_across_bus_publishes(tmp_path: Path) -> None:
    db = Database(tmp_path / "c.db")
    rid = db.add_alert_rule("Crowding", "CROWDING", min_count=1,
                            severity="warning", cooldown_s=60.0)
    assert rid > 0
    engine = AlertEngine(EventSummarizer(), _cam)
    engine.set_rules(db.list_alert_rules())
    count = []

    def on_event(event: Event) -> None:
        if engine.evaluate(event) is not None:
            count.append(1)

    bus = EventBus()
    bus.subscribe(None, on_event)
    for ts in (0.0, 10.0, 30.0, 70.0):     # fires at 0.0 and 70.0 only
        bus.publish(Event(type=EventType.CROWDING, timestamp=ts, source_id=1,
                          label="k", metadata={"count": 3}))
    assert len(count) == 2
