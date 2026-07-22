# tests/test_alert_db.py
from pathlib import Path

from alerts.model import Alert
from storage.database import Database


def _db(tmp_path: Path) -> Database:
    return Database(tmp_path / "c.db")


def test_alert_rule_crud(tmp_path: Path) -> None:
    db = _db(tmp_path)
    rid = db.add_alert_rule("Crowding", "CROWDING", min_count=8, severity="warning")
    rules = db.list_alert_rules()
    assert len(rules) == 1 and rules[0].id == rid
    assert rules[0].event_type == "CROWDING" and rules[0].min_count == 8
    assert rules[0].enabled is True
    db.update_alert_rule(rid, "Crowding", "CROWDING", None, None, 12, None, None,
                         "critical", 30.0, False)
    r = db.list_alert_rules()[0]
    assert r.min_count == 12 and r.severity == "critical" and r.enabled is False
    db.delete_alert_rule(rid)
    assert db.list_alert_rules() == []


def test_alert_crud_and_ack(tmp_path: Path) -> None:
    db = _db(tmp_path)
    alert = Alert(rule_id=1, rule_name="Terk", event_type="ABANDONED_OBJECT",
                  source_id=2, severity="critical", summary="Abandoned object.",
                  timestamp=100.0, snapshot_path="s.jpg", metadata={"label": "bag"})
    aid = db.add_alert(alert)
    got = db.list_alerts()
    assert len(got) == 1 and got[0].id == aid
    assert got[0].summary == "Abandoned object." and got[0].metadata["label"] == "bag"
    assert got[0].acknowledged is False
    db.acknowledge_alert(aid)
    assert db.list_alerts()[0].acknowledged is True
    assert db.list_alerts(unacked_only=True) == []


def test_seed_default_alert_rules_is_once(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.seed_default_alert_rules(crowd_min=8, cooldown_s=60.0)
    first = db.list_alert_rules()
    types = {r.event_type for r in first}
    assert types == {"ABANDONED_OBJECT", "RESTRICTED", "CROWDING", "LOITERING"}
    crowd = next(r for r in first if r.event_type == "CROWDING")
    assert crowd.min_count == 8 and crowd.severity == "warning"
    db.delete_alert_rule(first[0].id)          # user removes one
    db.seed_default_alert_rules(crowd_min=8, cooldown_s=60.0)   # must NOT re-seed
    assert len(db.list_alert_rules()) == len(first) - 1
