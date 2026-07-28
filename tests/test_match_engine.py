"""Integration tests for MatchEngine with the deterministic encoder and synthetic,
hand-built multi-source frames. Fully reproducible — no models, no I/O."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from match.anpr.voting import PlateVoter
from match.encoders.baseline import DeterministicEncoder
from match.engine import MatchEngine, SourceFrames
from match.scoring import ScoringConfig
from match.types import Query


@dataclass
class FakeDet:
    bbox: tuple[int, int, int, int]
    category: str
    confidence: float = 0.9


def pattern_a(h=80, w=40):
    """Red top / blue bottom — a structured, discriminative appearance."""
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[: h // 2] = (0, 0, 200)      # BGR red
    img[h // 2:] = (200, 0, 0)       # BGR blue
    return img


def solid(color, h=80, w=40):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = color
    return img


def full_frame_detector(category):
    def detect(frame):
        h, w = frame.shape[:2]
        return [FakeDet((0, 0, w, h), category)]
    return detect


def halves_detector(category):
    def detect(frame):
        h, w = frame.shape[:2]
        return [FakeDet((0, 0, w // 2, h), category),
                FakeDet((w // 2, 0, w, h), category)]
    return detect


def _engine(detect, **kw):
    return MatchEngine(
        encoders={"person": DeterministicEncoder(), "vehicle": DeterministicEncoder(),
                  "object": DeterministicEncoder()},
        detect=detect,
        scoring=ScoringConfig(min_temporal_support=2, accept_threshold=0.6),
        **kw,
    )


def test_winner_is_matching_source() -> None:
    eng = _engine(full_frame_detector("person"))
    q = Query(cls="person", crop=pattern_a())
    sources = [
        SourceFrames(1, "cam-A", [pattern_a(), pattern_a(), pattern_a()]),
        SourceFrames(2, "cam-B", [solid((0, 200, 0)), solid((0, 200, 0)), solid((0, 200, 0))]),
    ]
    res = eng.match(q, sources)
    assert len(res.hits) == 1
    assert res.hits[0].source_id == 1
    assert res.hits[0].score > 0.9
    # baseline trust caps confidence honestly
    assert res.hits[0].confidence <= DeterministicEncoder().trust + 1e-9
    assert res.models_used == ("baseline-grid-v1",)


def test_no_match_returns_empty() -> None:
    eng = _engine(full_frame_detector("person"))
    q = Query(cls="person", crop=pattern_a())
    sources = [SourceFrames(2, "cam-B", [solid((0, 200, 0))] * 3)]
    res = eng.match(q, sources)
    assert res.hits == ()


def test_temporal_support_required() -> None:
    eng = _engine(full_frame_detector("person"))
    q = Query(cls="person", crop=pattern_a())
    # only ONE matching frame -> support 1 < min_temporal_support 2 -> not asserted
    sources = [SourceFrames(1, "cam-A", [pattern_a(), solid((0, 200, 0))])]
    res = eng.match(q, sources)
    assert res.hits == ()


def test_deterministic_repeatable() -> None:
    eng = _engine(full_frame_detector("person"))
    q = Query(cls="person", crop=pattern_a())
    sources = [
        SourceFrames(1, "cam-A", [pattern_a()] * 3),
        SourceFrames(2, "cam-B", [pattern_a()] * 3),
    ]
    r1 = eng.match(q, sources)
    r2 = eng.match(q, sources)
    assert [(h.source_id, h.score, h.confidence) for h in r1.hits] == \
           [(h.source_id, h.score, h.confidence) for h in r2.hits]


def test_cross_source_ambiguity_flagged() -> None:
    eng = _engine(full_frame_detector("person"))
    q = Query(cls="person", crop=pattern_a())
    # two cameras with the SAME appearance -> equally likely -> ambiguous
    sources = [
        SourceFrames(1, "cam-A", [pattern_a()] * 3),
        SourceFrames(2, "cam-B", [pattern_a()] * 3),
    ]
    res = eng.match(q, sources)
    assert len(res.hits) == 2
    assert res.hits[0].ambiguous is True     # near-tie between the two cameras


def test_within_frame_ambiguity_flagged() -> None:
    eng = _engine(halves_detector("person"))
    q = Query(cls="person", crop=pattern_a())
    # each frame has TWO identical-looking candidates -> in-frame near-tie
    sources = [SourceFrames(1, "cam-A", [pattern_a()] * 3)]
    res = eng.match(q, sources)
    assert len(res.hits) == 1
    assert res.hits[0].ambiguous is True


def test_ordering_is_score_then_id() -> None:
    eng = _engine(full_frame_detector("person"))
    q = Query(cls="person", crop=pattern_a())
    sources = [
        SourceFrames(5, "cam-5", [pattern_a()] * 3),
        SourceFrames(3, "cam-3", [pattern_a()] * 3),
    ]
    res = eng.match(q, sources)
    # equal scores -> lower source_id first (stable tiebreak)
    assert [h.source_id for h in res.hits] == [3, 5]


def test_plate_short_circuit_is_definitive() -> None:
    def plate_reader(_crop):
        return [("34 ABC 123", 0.92)]
    eng = _engine(
        full_frame_detector("vehicle"),
        plate_reader=plate_reader,
        plate_voter=PlateVoter(min_agreement=3),
    )
    # appearance is a flat colour (weak/zero cosine) but the plate is read consistently
    q = Query(cls="vehicle", crop=solid((0, 200, 0)), plate="34ABC123")
    sources = [SourceFrames(1, "cam-A", [solid((0, 200, 0))] * 3)]
    res = eng.match(q, sources)
    assert len(res.hits) == 1
    hit = res.hits[0]
    assert hit.plate == "34ABC123"
    assert hit.evidence.plate_match is True
    assert hit.confidence >= 0.95          # definitive via plate
    assert hit.ambiguous is False


def test_accept_threshold_override_is_per_call() -> None:
    eng = _engine(full_frame_detector("person"))
    q = Query(cls="person", crop=pattern_a())
    # cam-B is a different (but non-degenerate) pattern that scores modestly
    other = np.zeros((80, 40, 3), dtype=np.uint8)
    other[:20] = (0, 0, 200)
    other[20:] = (0, 120, 60)
    sources = [SourceFrames(2, "cam-B", [other] * 3)]
    strict = eng.match(q, sources, accept_threshold=0.99)
    loose = eng.match(q, sources, accept_threshold=0.1)
    assert len(loose.hits) >= len(strict.hits)
    # the shared config is not mutated by the override
    assert eng.cfg.accept_threshold == 0.6


def test_inactive_single_frame_camera_matches() -> None:
    eng = _engine(full_frame_detector("person"))
    q = Query(cls="person", crop=pattern_a())
    # a single-frame window (an inactive camera's thumbnail) still yields a hit
    sources = [SourceFrames(1, "cam-A", [pattern_a()])]
    res = eng.match(q, sources)
    assert len(res.hits) == 1
    assert res.hits[0].evidence.temporal_support == 1


def test_wrong_class_never_matches() -> None:
    # query is a person but every detection is a vehicle -> nothing to compare
    eng = _engine(full_frame_detector("vehicle"))
    q = Query(cls="person", crop=pattern_a())
    sources = [SourceFrames(1, "cam-A", [pattern_a()] * 3)]
    res = eng.match(q, sources)
    assert res.hits == ()
