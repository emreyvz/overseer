"""Pure retrieval / recognition metrics. No numpy needed; plain Python for exactness.

Ranking metrics take, per query, the gallery labels ordered by descending similarity,
plus that query's true label. This decouples the metrics from any encoder."""
from __future__ import annotations

from typing import Sequence

from match.anpr.normalize import normalize_plate


def cmc_rank_k(ranked_labels: Sequence[Sequence], true_labels: Sequence, k: int) -> float:
    """Cumulative Match Characteristic at rank k: fraction of queries whose true label
    appears in the top-k retrieved labels."""
    if not true_labels:
        return 0.0
    hits = 0
    for ranked, truth in zip(ranked_labels, true_labels):
        if truth in list(ranked)[:k]:
            hits += 1
    return hits / len(true_labels)


def mean_ap(ranked_labels: Sequence[Sequence], true_labels: Sequence) -> float:
    """Mean Average Precision over queries. AP rewards ranking all relevant gallery
    items (same label) ahead of irrelevant ones."""
    aps: list[float] = []
    for ranked, truth in zip(ranked_labels, true_labels):
        ranked = list(ranked)
        num_rel = sum(1 for lbl in ranked if lbl == truth)
        if num_rel == 0:
            aps.append(0.0)
            continue
        hit = 0
        precisions: list[float] = []
        for i, lbl in enumerate(ranked, 1):
            if lbl == truth:
                hit += 1
                precisions.append(hit / i)
        aps.append(sum(precisions) / num_rel)
    return sum(aps) / len(aps) if aps else 0.0


def anpr_exact_match(preds: Sequence[str], truths: Sequence[str],
                     fold_confusable: bool = False) -> float:
    """Fraction of plate predictions that exactly equal the truth after normalization."""
    if not truths:
        return 0.0
    ok = 0
    for p, t in zip(preds, truths):
        if normalize_plate(p, fold_confusable) == normalize_plate(t, fold_confusable):
            ok += 1
    return ok / len(truths)


def precision_recall_at(scored: Sequence[tuple[float, bool]],
                        threshold: float) -> tuple[float, float]:
    """Given (score, is_true_match) pairs, precision & recall for accepting score >=
    threshold. Precision is 1.0 when nothing is accepted (vacuously correct); recall is
    0.0 when there are no true matches to find."""
    tp = sum(1 for s, t in scored if s >= threshold and t)
    fp = sum(1 for s, t in scored if s >= threshold and not t)
    total_true = sum(1 for _, t in scored if t)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / total_true if total_true > 0 else 0.0
    return (precision, recall)
