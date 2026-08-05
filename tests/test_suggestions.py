# tests/test_suggestions.py
from server.suggestions import alert_suggestions, camera_suggestions, coverage_suggestions


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


# ── FOG OF WAR blind spots as work items ────────────────────────────────────────────────────

def _spot(**kw) -> dict:
    base = {"id": 1, "kind": "occlusion", "name": "MID LEFT", "polygon": [[0, 0], [1, 0], [1, 1]],
            "area_m2": 14.2, "persistent": True, "events": 3,
            "remedies": [{"text": "MOVE OR REMOVE THE OBJECT AT MID LEFT", "recovers_m2": 14.2}]}
    base.update(kw)
    return base


def test_coverage_suggestion_states_its_evidence_and_a_remedy() -> None:
    out = coverage_suggestions({1: [_spot()]}, {1: "Yard"})
    assert len(out) == 1
    s = out[0]
    assert s["kind"] == "coverage" and s["cam"] == "Yard"
    assert "MID LEFT" in s["title"]
    assert "14 m2" in s["why"] and "estimated" in s["why"]   # never present an estimate as a fact
    assert "3 tracks already lost" in s["why"]
    assert "move or remove the object" in s["why"]
    assert s["spot"]["id"] == 1


def test_coverage_suggestions_are_capped_per_camera() -> None:
    spots = [_spot(id=i, name=f"SPOT {i}") for i in range(6)]
    out = coverage_suggestions({1: spots}, {1: "Yard"}, max_per_camera=3)
    assert len(out) == 3


def test_a_spot_with_no_area_still_produces_a_suggestion() -> None:
    out = coverage_suggestions({1: [_spot(kind="empirical", area_m2=None, events=0)]}, {1: "Yard"})
    assert len(out) == 1
    assert "m2" not in out[0]["why"]
    assert "losing people" in out[0]["why"]
