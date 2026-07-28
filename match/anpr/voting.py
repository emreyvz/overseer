"""Temporal voting over per-frame plate reads. Pure and deterministic.

A plate is only asserted when the SAME normalized string is read across at least
``min_agreement`` frames — one blurry misread can never become an identity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from match.anpr.normalize import normalize_plate


@dataclass(frozen=True)
class PlateVoter:
    min_agreement: int = 3

    def vote(self, reads: Sequence[tuple[str, float]],
             fold_confusable: bool = False) -> tuple[str | None, float]:
        """``reads`` is a sequence of (raw_text, ocr_confidence). Returns the winning
        (normalized_plate, mean_confidence), or (None, 0.0) if no plate reached
        ``min_agreement``. Ties are broken deterministically by total confidence, then
        count, then the plate string itself."""
        buckets: dict[str, list[float]] = {}
        for raw, conf in reads:
            p = normalize_plate(raw, fold_confusable)
            if not p:
                continue
            buckets.setdefault(p, []).append(float(conf))
        if not buckets:
            return (None, 0.0)
        plate, confs = max(
            buckets.items(),
            key=lambda kv: (sum(kv[1]), len(kv[1]), kv[0]),
        )
        if len(confs) < self.min_agreement:
            return (None, 0.0)
        return (plate, sum(confs) / len(confs))
