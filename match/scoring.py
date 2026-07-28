"""The deterministic scoring contract. Pure functions only — same inputs always give
the same outputs, so the whole match pipeline is reproducible and unit-testable.

The contract, in order:
  1. per-frame score      = cosine(query, candidate)            in [-1, 1]
  2. window aggregation    = trimmed high-percentile over the frames that clear a floor,
                            valid only with enough temporal support (the "stable" rule)
  3. margin                = best source score - runner-up source score
  4. confidence            = monotonic calibration to [0, 1], capped by encoder trust
  5. plate short-circuit   = a confident plate match pins confidence to a definitive ceiling
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class ScoringConfig:
    frame_floor: float = 0.5           # a per-frame cosine below this is not "support"
    min_temporal_support: int = 2      # need >= this many supporting frames to assert a score
    aggregate_percentile: float = 80.0 # high-percentile (trimmed) aggregation over support
    accept_threshold: float = 0.6      # aggregated score below this => not a match
    min_margin: float = 0.06           # best - runner below this => ambiguous
    plate_definitive_confidence: float = 0.98  # confidence ceiling on a plate match
    # confidence calibration weights — must sum to 1.0 so raw stays in [0,1]
    w_score: float = 0.5
    w_margin: float = 0.2
    w_det: float = 0.1
    w_mask: float = 0.1
    w_support: float = 0.1


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def cosine(a: Sequence[float] | np.ndarray, b: Sequence[float] | np.ndarray) -> float:
    """Cosine similarity, re-normalizing defensively. Returns -1.0 for empty/mismatched
    or zero vectors (a definitively 'no similarity' sentinel)."""
    a = np.asarray(a, dtype=np.float64).reshape(-1)
    b = np.asarray(b, dtype=np.float64).reshape(-1)
    if a.size == 0 or b.size == 0 or a.shape != b.shape:
        return -1.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return -1.0
    return float(np.dot(a, b) / (na * nb))


def required_support(n_candidate_frames: int, cfg: ScoringConfig) -> int:
    """How many supporting frames are needed to assert a match. Normally
    ``min_temporal_support``, but never more than the number of frames actually available
    for this source: an inactive camera we only have a single frame for must still be
    searchable, and its single frame IS all the evidence there is (temporal_support in the
    hit's evidence tells the operator the match rests on one frame)."""
    return min(cfg.min_temporal_support, max(1, n_candidate_frames))


def aggregate_window(scores: Sequence[float], cfg: ScoringConfig) -> tuple[float, int]:
    """Aggregate per-frame scores into one stable window score.

    Returns (aggregated_score, support_count). ``support_count`` is the number of frames
    at or above ``frame_floor``. If support is below ``required_support`` the match is not
    asserted and the score is -1.0 — one lucky frame among many can never win, but a source
    that only offered a few frames is judged against what it offered. Aggregation is a
    ``'lower'``-interpolated percentile so the result is always an observed value and thus
    exactly reproducible across platforms."""
    s = [float(x) for x in scores]
    supporting = sorted(x for x in s if x >= cfg.frame_floor)
    support = len(supporting)
    if support < required_support(len(s), cfg):
        return (-1.0, support)
    val = float(np.percentile(supporting, cfg.aggregate_percentile, method="lower"))
    return (val, support)


def margin(best: float, runner: float | None) -> float:
    """Gap between the best source and the runner-up. 1.0 when there is no runner-up
    (a single candidate is, by definition, unambiguous)."""
    if runner is None or runner < 0.0:
        return 1.0
    return best - runner


def is_ambiguous(best: float, runner: float | None, cfg: ScoringConfig) -> bool:
    """A near-tie between the top two sources is honestly flagged, not asserted."""
    if runner is None or runner < 0.0:
        return False
    return (best - runner) < cfg.min_margin


def accept(score: float, cfg: ScoringConfig) -> bool:
    return score >= cfg.accept_threshold


def confidence(
    *,
    score: float,
    margin_val: float,
    det_conf: float,
    mask_coverage: float,
    trust: float,
    support: int,
    cfg: ScoringConfig,
    plate_match: bool = False,
) -> float:
    """Calibrated confidence in [0,1]. Monotonic non-decreasing in every quality signal.
    Capped by encoder ``trust`` so a low-trust baseline encoder can never report high
    confidence. A confident plate match overrides appearance entirely."""
    if plate_match:
        return cfg.plate_definitive_confidence
    s_norm = _clamp01((score + 1.0) / 2.0)                 # cosine [-1,1] -> [0,1]
    m_norm = _clamp01(margin_val / 0.3)                    # margin saturates at 0.3
    d_norm = _clamp01(det_conf)
    k_norm = _clamp01(mask_coverage)
    sup_norm = _clamp01(support / max(1, cfg.min_temporal_support * 2))
    raw = (cfg.w_score * s_norm + cfg.w_margin * m_norm + cfg.w_det * d_norm
           + cfg.w_mask * k_norm + cfg.w_support * sup_norm)
    return _clamp01(min(raw, trust))
