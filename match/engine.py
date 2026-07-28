"""The match engine: given a query and, per source, a rolling window of frames, produce a
ranked, evidence-bearing, deterministic result.

Design points that make it reliable and testable:
  * No I/O. It receives already-captured frames and a detector callable, so with a
    deterministic encoder it is fully reproducible — the golden tests exercise exactly it.
  * Same-class only. It compares a person query to person detections, a car to cars, etc.,
    so it never locks onto the sky or a wall that merely shares a colour.
  * Stable over the window. Per source it takes the best candidate score in each frame,
    then aggregates across the window with a temporal-support floor: one lucky frame can't
    win (see scoring.aggregate_window).
  * Honest margins. It flags both an in-frame near-tie (two similar objects in one camera)
    and a cross-source near-tie (two cameras equally likely) rather than asserting.
  * Definitive plates. For vehicles, a confident plate that matches the query's known plate
    pins the result to identity, overriding appearance.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from match.anpr.normalize import plates_match
from match.anpr.voting import PlateVoter
from match.encoders.base import Encoder
from match.scoring import (
    ScoringConfig,
    aggregate_window,
    confidence,
    cosine,
    is_ambiguous,
    margin,
)
from match.segmentation import Segmenter
from match.types import Evidence, MatchHit, MatchResult, Query

_DEFAULT_CAT2CLS = {
    "person": "person", "vehicle": "vehicle", "animal": "animal",
    "accessory": "object", "motion": "object", "weapon": "object", "object": "object",
}


@dataclass(frozen=True)
class SourceFrames:
    source_id: int
    source_name: str
    frames: list[np.ndarray]


@dataclass(frozen=True)
class _SourceScore:
    source_id: int
    source_name: str
    score: float
    support: int
    bbox: tuple[int, int, int, int]
    frame_hw: tuple[int, int]
    det_conf: float
    mask_coverage: float
    within_margin: float
    within_ambiguous: bool
    plate: str | None
    plate_conf: float


class MatchEngine:
    def __init__(
        self,
        *,
        encoders: dict[str, Encoder],
        detect,                                  # callable(frame_bgr) -> list[detection]
        segmenter: Segmenter | None = None,
        scoring: ScoringConfig | None = None,
        plate_reader=None,                       # callable(crop) -> list[(text, conf)] | None
        plate_voter: PlateVoter | None = None,
        category_to_cls: dict[str, str] | None = None,
    ) -> None:
        self.encoders = encoders
        self._detect = detect
        self.seg = segmenter or Segmenter()
        self.cfg = scoring or ScoringConfig()
        self.plate_reader = plate_reader
        self.plate_voter = plate_voter or PlateVoter()
        self.cat2cls = category_to_cls or dict(_DEFAULT_CAT2CLS)

    def _encoder_for(self, cls: str) -> Encoder | None:
        return self.encoders.get(cls) or self.encoders.get("object")

    def _candidates(self, frame: np.ndarray, want_cls: str):
        """Same-class detections in a frame, as (bbox, det_conf, crop)."""
        out = []
        h, w = frame.shape[:2]
        for d in self._detect(frame) or []:
            cls = self.cat2cls.get(getattr(d, "category", "object"), "object")
            if cls != want_cls:
                continue
            x1, y1, x2, y2 = (int(v) for v in d.bbox)
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(w, x2), min(h, y2)
            if x2 <= x1 or y2 <= y1:
                continue
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            out.append(((x1, y1, x2, y2), float(getattr(d, "confidence", 1.0)), crop))
        return out

    def _read_plates(self, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        if self.plate_reader is None:
            return []
        reads: list[tuple[str, float]] = []
        for crop in crops:
            try:
                for text, conf in (self.plate_reader(crop) or []):
                    reads.append((str(text), float(conf)))
            except Exception:  # noqa: BLE001 - OCR must never break a search
                pass
        return reads

    def _match_source(self, query: Query, src: SourceFrames, qv: np.ndarray,
                      enc: Encoder) -> _SourceScore | None:
        per_frame_best: list[float] = []
        best_score = -2.0
        best_meta: tuple | None = None      # (bbox, det_conf, frame_hw, coverage)
        best_within_margin = 1.0
        best_within_amb = False
        plate_reads: list[tuple[str, float]] = []
        want = query.cls

        for frame in src.frames:
            cands = self._candidates(frame, want)
            if not cands:
                continue
            crops = [c[2] for c in cands]
            masks: list[np.ndarray] = []
            covs: list[float] = []
            for crop in crops:
                m, cov = self.seg.mask(crop, want)
                masks.append(m)
                covs.append(cov)
            vecs = enc.encode(crops, masks)
            sims = [cosine(qv, vecs[i]) for i in range(vecs.shape[0])]
            if not sims:
                continue
            order = sorted(range(len(sims)), key=lambda i: sims[i], reverse=True)
            top = order[0]
            frame_best = sims[top]
            per_frame_best.append(frame_best)
            runner = sims[order[1]] if len(order) > 1 else None
            if want == "vehicle":
                plate_reads.extend(self._read_plates(crops))
            if frame_best > best_score:
                best_score = frame_best
                bbox, det_conf, _ = cands[top]
                best_meta = (bbox, det_conf, frame.shape[:2], covs[top])
                best_within_margin = margin(frame_best, runner)
                best_within_amb = is_ambiguous(frame_best, runner, self.cfg)

        if best_meta is None:
            return None                   # no same-class candidate anywhere in the window
        agg_score, support = aggregate_window(per_frame_best, self.cfg)
        # We return even when appearance had no temporal support (agg_score < 0): for
        # vehicles a voted plate can still make this a definitive hit. match() decides
        # acceptance from score OR plate.
        bbox, det_conf, frame_hw, coverage = best_meta
        plate, plate_conf = self.plate_voter.vote(plate_reads) if plate_reads else (None, 0.0)
        return _SourceScore(
            source_id=src.source_id, source_name=src.source_name, score=agg_score,
            support=support, bbox=bbox, frame_hw=frame_hw, det_conf=det_conf,
            mask_coverage=coverage, within_margin=best_within_margin,
            within_ambiguous=best_within_amb, plate=plate, plate_conf=plate_conf,
        )

    def match(self, query: Query, sources: list[SourceFrames]) -> MatchResult:
        warnings: list[str] = []
        enc = self._encoder_for(query.cls)
        if enc is None or not enc.available():
            return MatchResult(query_cls=query.cls,
                               warnings=("no encoder available for class",))
        if not enc.available():  # pragma: no cover - defensive
            warnings.append("encoder unavailable")
        if query.crop is None or query.crop.size == 0:
            return MatchResult(query_cls=query.cls, warnings=("empty query crop",))

        qmask, _ = self.seg.mask(query.crop, query.cls)
        qmat = enc.encode([query.crop], [qmask])
        if qmat.shape[0] == 0:
            return MatchResult(query_cls=query.cls, warnings=("query failed to encode",))
        qv = qmat[0]

        scored: list[_SourceScore] = []
        for src in sources:
            s = self._match_source(query, src, qv, enc)
            if s is None:
                continue
            plate_ok = bool(query.plate and s.plate
                            and plates_match(query.plate, s.plate))
            if s.score >= self.cfg.accept_threshold or plate_ok:
                scored.append(s)

        # deterministic ordering: score desc, then source_id asc as a stable tiebreak
        scored.sort(key=lambda s: (-s.score, s.source_id))

        hits: list[MatchHit] = []
        for rank, s in enumerate(scored):
            runner = scored[rank + 1].score if rank + 1 < len(scored) else None
            cross_margin = margin(s.score, runner)
            cross_amb = is_ambiguous(s.score, runner, self.cfg)
            plate_match = bool(
                query.plate and s.plate and plates_match(query.plate, s.plate)
            )
            eff_margin = min(s.within_margin, cross_margin)
            ambiguous = bool(s.within_ambiguous or cross_amb) and not plate_match
            conf = confidence(
                score=s.score, margin_val=eff_margin, det_conf=s.det_conf,
                mask_coverage=s.mask_coverage, trust=enc.trust, support=s.support,
                cfg=self.cfg, plate_match=plate_match,
            )
            x1, y1, x2, y2 = s.bbox
            fh, fw = s.frame_hw
            bbox_norm = (x1 / fw, y1 / fh, (x2 - x1) / fw, (y2 - y1) / fh)
            evidence = Evidence(
                score=round(s.score, 4), margin=round(eff_margin, 4),
                det_conf=round(s.det_conf, 4), mask_coverage=round(s.mask_coverage, 4),
                temporal_support=s.support, model_id=enc.model_id, trust=enc.trust,
                plate=s.plate, plate_conf=round(s.plate_conf, 4), plate_match=plate_match,
            )
            hits.append(MatchHit(
                source_id=s.source_id, source_name=s.source_name, cls=query.cls,
                bbox_norm=bbox_norm, score=round(s.score, 4), confidence=round(conf, 4),
                margin=round(eff_margin, 4), ambiguous=ambiguous, plate=s.plate,
                evidence=evidence,
            ))
        return MatchResult(
            hits=tuple(hits), query_cls=query.cls,
            models_used=(enc.model_id,), warnings=tuple(warnings),
        )
