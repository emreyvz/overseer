from match.eval.metrics import (
    anpr_exact_match,
    cmc_rank_k,
    mean_ap,
    precision_recall_at,
)


def test_cmc_perfect() -> None:
    ranked = [["a", "b", "c"], ["b", "a", "c"]]
    truth = ["a", "b"]
    assert cmc_rank_k(ranked, truth, 1) == 1.0


def test_cmc_rank1_miss_rank2_hit() -> None:
    ranked = [["b", "a", "c"]]
    truth = ["a"]
    assert cmc_rank_k(ranked, truth, 1) == 0.0
    assert cmc_rank_k(ranked, truth, 2) == 1.0


def test_cmc_empty() -> None:
    assert cmc_rank_k([], [], 1) == 0.0


def test_map_perfect_ranking() -> None:
    # all relevant items ranked first -> AP 1.0
    ranked = [["a", "a", "b", "c"]]
    truth = ["a"]
    assert abs(mean_ap(ranked, truth) - 1.0) < 1e-9


def test_map_partial() -> None:
    # relevant at positions 1 and 3: AP = (1/1 + 2/3)/2
    ranked = [["a", "b", "a", "c"]]
    truth = ["a"]
    assert abs(mean_ap(ranked, truth) - (1.0 + 2 / 3) / 2) < 1e-9


def test_map_no_relevant() -> None:
    assert mean_ap([["b", "c"]], ["a"]) == 0.0


def test_anpr_exact_match() -> None:
    preds = ["34 ABC 123", "99xyz999", "wrong"]
    truths = ["34ABC123", "99XYZ999", "right"]
    assert abs(anpr_exact_match(preds, truths) - 2 / 3) < 1e-9


def test_precision_recall() -> None:
    scored = [(0.9, True), (0.8, True), (0.7, False), (0.4, True)]
    # threshold 0.75: tp=2 (0.9,0.8), fp=0, total_true=3 -> P=1.0, R=2/3
    p, r = precision_recall_at(scored, 0.75)
    assert p == 1.0
    assert abs(r - 2 / 3) < 1e-9


def test_precision_recall_nothing_accepted() -> None:
    scored = [(0.1, True)]
    p, r = precision_recall_at(scored, 0.9)
    assert p == 1.0  # vacuously precise
    assert r == 0.0
