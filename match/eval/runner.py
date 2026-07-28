"""Evaluation runner: measure an encoder's ReID accuracy and the ANPR voter's exact-match
on labeled data, emit a JSON report. This is the verifiability deliverable — accuracy is a
number you can watch in CI, not a claim.

    uv run python -m match.eval.runner                 # synthetic ReID + bundled ANPR
    uv run python -m match.eval.runner --reid-manifest data/reid.json
    uv run python -m match.eval.runner --anpr-fixture data/plates.json
"""
from __future__ import annotations

import argparse
import json

from match.anpr.voting import PlateVoter
from match.encoders.base import Encoder
from match.encoders.baseline import DeterministicEncoder
from match.eval.dataset import (
    DEFAULT_ANPR_FIXTURE,
    AnprCase,
    LabeledCrop,
    load_anpr_fixture,
    load_reid_manifest,
    synthetic_reid,
)
from match.eval.metrics import anpr_exact_match, cmc_rank_k, mean_ap
from match.scoring import cosine


def reid_metrics(encoder: Encoder, queries: list[LabeledCrop],
                 gallery: list[LabeledCrop]) -> dict:
    """Rank each gallery item per query by cosine, then compute CMC and mAP."""
    g_vecs = encoder.encode([g.crop for g in gallery])
    g_labels = [g.label for g in gallery]
    q_vecs = encoder.encode([q.crop for q in queries])
    ranked_labels: list[list[str]] = []
    for i in range(q_vecs.shape[0]):
        sims = [cosine(q_vecs[i], g_vecs[j]) for j in range(g_vecs.shape[0])]
        order = sorted(range(len(sims)), key=lambda j: sims[j], reverse=True)
        ranked_labels.append([g_labels[j] for j in order])
    truth = [q.label for q in queries]
    return {
        "n_queries": len(queries),
        "n_gallery": len(gallery),
        "rank1": round(cmc_rank_k(ranked_labels, truth, 1), 4),
        "rank5": round(cmc_rank_k(ranked_labels, truth, 5), 4),
        "mAP": round(mean_ap(ranked_labels, truth), 4),
        "model_id": encoder.model_id,
    }


def anpr_metrics(cases: list[AnprCase], voter: PlateVoter | None = None,
                 fold_confusable: bool = True) -> dict:
    voter = voter or PlateVoter(min_agreement=2)
    preds: list[str] = []
    truths: list[str] = []
    for case in cases:
        plate, _conf = voter.vote(case.reads, fold_confusable=fold_confusable)
        preds.append(plate or "")
        truths.append(case.plate)
    return {
        "n_cases": len(cases),
        "exact_match": round(anpr_exact_match(preds, truths, fold_confusable), 4),
        "min_agreement": voter.min_agreement,
    }


def run(reid_manifest: str | None = None, anpr_fixture: str | None = None,
        encoder: Encoder | None = None) -> dict:
    encoder = encoder or DeterministicEncoder()
    if reid_manifest:
        queries, gallery = load_reid_manifest(reid_manifest)
    else:
        queries, gallery = synthetic_reid()
    cases = load_anpr_fixture(anpr_fixture or DEFAULT_ANPR_FIXTURE)
    return {
        "reid": reid_metrics(encoder, queries, gallery),
        "anpr": anpr_metrics(cases),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Match engine evaluation harness")
    ap.add_argument("--reid-manifest", default=None,
                    help="JSON manifest of real ReID crops; omit for synthetic")
    ap.add_argument("--anpr-fixture", default=None,
                    help="JSON of ANPR cases; omit for the bundled fixture")
    args = ap.parse_args()
    report = run(args.reid_manifest, args.anpr_fixture)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
