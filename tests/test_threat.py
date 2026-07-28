from dataclasses import dataclass

from alerts.threat import ThreatScorer, escalate, level_severity


@dataclass
class _Type:
    name: str


@dataclass
class _Ev:
    type: _Type
    source_id: int
    timestamp: float


def ev(name: str, ts: float, src: int = 1) -> _Ev:
    return _Ev(_Type(name), src, ts)


def test_benign_event_stays_low() -> None:
    s = ThreatScorer()
    a = s.observe(ev("LOITERING", 0.0))
    assert a.level in ("none", "low") and a.severity == "info" and not a.combo


def test_single_running_is_not_a_threat() -> None:
    s = ThreatScorer()
    a = s.observe(ev("RUNNING", 0.0))
    assert not a.combo and a.severity == "info"


def test_restricted_plus_running_escalates() -> None:
    s = ThreatScorer()
    s.observe(ev("RESTRICTED", 0.0))
    a = s.observe(ev("RUNNING", 1.0))
    assert a.combo and a.level in ("high", "critical")
    assert a.severity == "critical"
    assert any("intrusion" in r for r in a.reasons)


def test_fighting_in_crowd_is_critical() -> None:
    s = ThreatScorer()
    s.observe(ev("CROWDING", 0.0))
    a = s.observe(ev("FIGHTING", 1.0))
    assert a.level == "critical" and a.severity == "critical" and a.combo


def test_signals_expire_out_of_window() -> None:
    s = ThreatScorer(window_s=10.0)
    s.observe(ev("RESTRICTED", 0.0))
    a = s.observe(ev("RUNNING", 30.0))   # restricted long gone -> no combo
    assert not a.combo and a.level in ("none", "low")


def test_per_camera_isolation() -> None:
    s = ThreatScorer()
    s.observe(ev("RESTRICTED", 0.0, src=1))
    a = s.observe(ev("RUNNING", 1.0, src=2))   # different camera -> no combo
    assert not a.combo


def test_tampering_combo() -> None:
    s = ThreatScorer()
    s.observe(ev("OBSTRUCTION", 0.0))
    a = s.observe(ev("DEFOCUS", 1.0))
    assert a.combo and any("tampering" in r for r in a.reasons)


def test_assess_without_observe_and_reset() -> None:
    s = ThreatScorer(window_s=10.0)
    s.observe(ev("FIGHTING", 0.0))
    standing = s.assess(1, now=2.0)
    assert standing.score > 0
    faded = s.assess(1, now=100.0)               # window emptied
    assert faded.level == "none"
    s.reset()
    assert s.assess(1, now=0.0).level == "none"


def test_escalate_and_level_severity() -> None:
    assert level_severity("high") == "critical"
    assert level_severity("elevated") == "warning"
    assert escalate("info", "elevated") == "warning"
    assert escalate("critical", "low") == "critical"   # never downgrade a rule's severity
