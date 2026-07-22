# tests/test_alert_model.py
from alerts.model import (
    SEVERITY_LABELS, SEVERITY_ORDER, Alert, AlertRule, severity_rank,
)


def test_severity_rank_orders_critical_highest() -> None:
    assert severity_rank("critical") > severity_rank("warning") > severity_rank("info")
    assert severity_rank("bogus") == 0


def test_severity_labels_cover_all_levels() -> None:
    assert set(SEVERITY_LABELS) == set(SEVERITY_ORDER) == {"info", "warning", "critical"}


def test_alert_defaults() -> None:
    a = Alert(rule_id=1, rule_name="r", event_type="CROWDING", source_id=3,
              severity="warning", summary="s", timestamp=10.0)
    assert a.id == 0 and a.acknowledged is False and a.metadata == {}


def test_alert_rule_fields() -> None:
    r = AlertRule(id=1, name="r", event_type="RESTRICTED", source_id=None,
                  zone_id=5, min_count=None, min_duration_s=None,
                  min_confidence=None, severity="critical", cooldown_s=60.0,
                  enabled=True)
    assert r.zone_id == 5 and r.severity == "critical"
