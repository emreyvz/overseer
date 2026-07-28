"""Typed contracts for the match engine. Frozen dataclasses so results are immutable
and easy to serialize/compare in tests."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, eq=False)
class Query:
    """What the operator is looking for: a class + an appearance crop, and (vehicles)
    an optional known plate string that makes a plate match definitive."""
    cls: str                 # person | vehicle | animal | object
    crop: np.ndarray         # BGR image of the entity
    plate: str | None = None


@dataclass(frozen=True, eq=False)
class Candidate:
    """A same-class detection in one source's frame, considered as a match target."""
    source_id: int
    source_name: str
    cls: str
    bbox: tuple[int, int, int, int]        # x1,y1,x2,y2 in frame pixels
    det_conf: float
    frame_hw: tuple[int, int]              # (h, w) of the frame it came from


@dataclass(frozen=True)
class Evidence:
    """Everything needed to justify a hit — surfaced to the API/UI so a result is
    self-explaining rather than an unexplained number."""
    score: float                 # aggregated cosine over the rolling window
    margin: float                # best - runner-up (1.0 when no runner-up)
    det_conf: float
    mask_coverage: float
    temporal_support: int        # how many frames supported the match
    model_id: str
    trust: float
    plate: str | None = None
    plate_conf: float = 0.0
    plate_match: bool = False


@dataclass(frozen=True)
class MatchHit:
    source_id: int
    source_name: str
    cls: str
    bbox_norm: tuple[float, float, float, float]   # x, y, w, h normalized [0,1]
    score: float
    confidence: float            # calibrated [0,1]
    margin: float
    ambiguous: bool
    plate: str | None
    evidence: Evidence


@dataclass(frozen=True)
class MatchResult:
    hits: tuple[MatchHit, ...] = ()
    query_cls: str = ""
    models_used: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
