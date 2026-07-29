# tests/test_suggestions.py
from server.suggestions import alert_suggestions, camera_suggestions


def test_alert_suggestion_fires_for_uncovered_behaviour() -> None:
    names = {1: "Street"}
    counts = {1: {"U_TURN": 6, "person": 400}}  # non-behaviour counts are ignored
    out = alert_suggestions(counts, names, set(), min_events=5, retention_days=7)
    assert len(out) == 1
    s = out[0]
    assert s["kind"] == "alert" and s["cam"] == "Street" and s["count"] == 6
    assert s["rule"]["event_type"] == "U_TURN"
    assert s["rule"]["source_id"] == 1
    assert s["rule"]["severity"] == "warning"
    assert "u turn" in s["title"]


def test_below_threshold_does_not_fire() -> None:
    out = alert_suggestions({1: {"U_TURN": 4}}, {1: "Street"}, set(),
                            min_events=5, retention_days=7)
    assert out == []


def test_existing_rule_suppresses_suggestion() -> None:
    counts = {1: {"LOITERING": 20}}
    names = {1: "Street"}
    # per-camera rule
    assert alert_suggestions(counts, names, {("LOITERING", 1)},
                             min_events=5, retention_days=7) == []
    # global rule (source_id None) also suppresses
    assert alert_suggestions(counts, names, {("LOITERING", None)},
                             min_events=5, retention_days=7) == []
    # an unrelated rule does NOT suppress
    assert len(alert_suggestions(counts, names, {("RUNNING", None)},
                                 min_events=5, retention_days=7)) == 1


def test_critical_behaviour_gets_critical_severity() -> None:
    out = alert_suggestions({1: {"FIGHTING": 8}}, {1: "Plaza"}, set(),
                            min_events=5, retention_days=7)
    assert out[0]["rule"]["severity"] == "critical"


def test_camera_suggestions_reputation_and_lighting() -> None:
    profiles = [
        {"id": 1, "name": "Alley", "frames": 300, "reconnects": 5,
         "dna": ["night dominant", "low light"], "reputation": 0.2,
         "person": 15, "vehicle": 3},
        {"id": 2, "name": "Lobby", "frames": 300, "reconnects": 0,
         "dna": ["pedestrian heavy"], "reputation": 0.9, "person": 40, "vehicle": 0},
    ]
    out = camera_suggestions(profiles)
    titles = {s["title"] for s in out}
    assert "Frequent disconnects at Alley" in titles
    assert "Poor lighting at Alley" in titles
    assert "Low detection quality at Alley" in titles
    # the healthy Lobby camera produces no advisories
    assert not any("Lobby" in t for t in titles)
    assert all(s["kind"] == "camera" for s in out)


def test_camera_with_too_few_frames_is_skipped() -> None:
    profiles = [{"id": 1, "name": "New", "frames": 10, "reconnects": 9,
                 "dna": ["low light"], "reputation": 0.1, "person": 50, "vehicle": 0}]
    assert camera_suggestions(profiles) == []
