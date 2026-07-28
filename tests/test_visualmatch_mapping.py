"""The /api/visualmatch payload contract: backend maps a MatchHit to the legacy shape
(camId, cam, score, cls, margin, ambiguous, bbox) PLUS the new evidence fields, so the
existing UI keeps working while gaining confidence/plate/evidence."""
from server.backend import Backend
from match.types import Evidence, MatchHit


def _hit() -> MatchHit:
    ev = Evidence(score=0.83, margin=0.2, det_conf=0.9, mask_coverage=0.62,
                  temporal_support=4, model_id="reid-person", trust=0.92,
                  plate=None, plate_conf=0.0, plate_match=False)
    return MatchHit(source_id=7, source_name="gate-cam", cls="person",
                    bbox_norm=(0.1, 0.2, 0.3, 0.4), score=0.83, confidence=0.77,
                    margin=0.2, ambiguous=False, plate=None, evidence=ev)


def test_hit_to_dict_is_backward_compatible() -> None:
    d = Backend._hit_to_dict(_hit())
    # legacy fields the current frontend already reads
    assert d["camId"] == "7"
    assert d["cam"] == "gate-cam"
    assert d["cls"] == "person"
    assert d["score"] == 0.83
    assert d["margin"] == 0.2
    assert d["ambiguous"] is False
    assert d["bbox"] == [0.1, 0.2, 0.3, 0.4]


def test_hit_to_dict_adds_evidence() -> None:
    d = Backend._hit_to_dict(_hit())
    assert d["confidence"] == 0.77
    assert d["plate"] is None
    ev = d["evidence"]
    assert ev["temporal_support"] == 4
    assert ev["model_id"] == "reid-person"
    assert ev["trust"] == 0.92
    assert ev["plate_match"] is False
