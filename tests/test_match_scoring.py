import numpy as np

from match.scoring import (
    ScoringConfig,
    accept,
    aggregate_window,
    confidence,
    cosine,
    is_ambiguous,
    margin,
)


def test_cosine_identical_is_one() -> None:
    v = np.array([1.0, 2.0, 3.0])
    assert cosine(v, v) == 1.0


def test_cosine_orthogonal_is_zero() -> None:
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_cosine_is_scale_invariant_and_deterministic() -> None:
    a = [1.0, 2.0, 2.0]
    b = [2.0, 4.0, 4.0]
    assert abs(cosine(a, b) - 1.0) < 1e-12
    assert cosine(a, b) == cosine(a, b)


def test_cosine_bad_inputs_return_sentinel() -> None:
    assert cosine([], [1.0]) == -1.0
    assert cosine([1.0, 2.0], [1.0]) == -1.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == -1.0


def test_aggregate_requires_temporal_support() -> None:
    cfg = ScoringConfig(frame_floor=0.5, min_temporal_support=2)
    # only one frame clears the floor -> not asserted
    score, support = aggregate_window([0.9, 0.1, 0.2], cfg)
    assert score == -1.0
    assert support == 1


def test_aggregate_stable_over_window() -> None:
    cfg = ScoringConfig(frame_floor=0.5, min_temporal_support=2, aggregate_percentile=80.0)
    score, support = aggregate_window([0.7, 0.8, 0.9, 0.3], cfg)
    assert support == 3
    # 'lower' percentile is always an observed value
    assert score in (0.7, 0.8, 0.9)


def test_aggregate_percentile_is_reproducible() -> None:
    cfg = ScoringConfig(frame_floor=0.0, min_temporal_support=1, aggregate_percentile=50.0)
    scores = [0.1, 0.4, 0.5, 0.9]
    assert aggregate_window(scores, cfg) == aggregate_window(list(reversed(scores)), cfg)


def test_single_frame_source_is_searchable() -> None:
    # an inactive camera offering ONE frame must still qualify on that one frame
    cfg = ScoringConfig(frame_floor=0.5, min_temporal_support=2)
    score, support = aggregate_window([0.9], cfg)
    assert support == 1
    assert score == 0.9


def test_one_lucky_frame_among_many_still_rejected() -> None:
    cfg = ScoringConfig(frame_floor=0.5, min_temporal_support=2)
    # five candidate frames, only one clears the floor -> still not asserted
    score, support = aggregate_window([0.9, 0.1, 0.2, 0.3, 0.1], cfg)
    assert score == -1.0
    assert support == 1


def test_margin_and_ambiguity() -> None:
    cfg = ScoringConfig(min_margin=0.06)
    assert margin(0.9, None) == 1.0
    assert abs(margin(0.9, 0.8) - 0.1) < 1e-12
    assert is_ambiguous(0.90, 0.88, cfg) is True
    assert is_ambiguous(0.90, 0.70, cfg) is False
    assert is_ambiguous(0.90, None, cfg) is False


def test_accept_threshold() -> None:
    cfg = ScoringConfig(accept_threshold=0.6)
    assert accept(0.6, cfg) is True
    assert accept(0.59, cfg) is False


def test_confidence_capped_by_trust() -> None:
    cfg = ScoringConfig()
    hi = confidence(score=1.0, margin_val=1.0, det_conf=1.0, mask_coverage=1.0,
                    trust=0.35, support=10, cfg=cfg)
    assert hi <= 0.35 + 1e-9  # baseline trust caps confidence


def test_confidence_monotonic_in_score() -> None:
    cfg = ScoringConfig()
    lo = confidence(score=0.6, margin_val=0.1, det_conf=0.8, mask_coverage=0.8,
                    trust=0.95, support=3, cfg=cfg)
    hi = confidence(score=0.95, margin_val=0.1, det_conf=0.8, mask_coverage=0.8,
                    trust=0.95, support=3, cfg=cfg)
    assert hi >= lo


def test_confidence_plate_short_circuit() -> None:
    cfg = ScoringConfig(plate_definitive_confidence=0.98)
    c = confidence(score=-1.0, margin_val=0.0, det_conf=0.0, mask_coverage=0.0,
                   trust=0.1, support=0, cfg=cfg, plate_match=True)
    assert c == 0.98  # a confident plate overrides weak appearance entirely


def test_confidence_in_unit_range() -> None:
    cfg = ScoringConfig()
    for s in (-1.0, 0.0, 0.7, 1.0):
        c = confidence(score=s, margin_val=0.5, det_conf=0.5, mask_coverage=0.5,
                       trust=1.0, support=4, cfg=cfg)
        assert 0.0 <= c <= 1.0
