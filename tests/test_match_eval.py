from match.encoders.baseline import DeterministicEncoder
from match.eval.dataset import load_anpr_fixture, synthetic_reid, DEFAULT_ANPR_FIXTURE
from match.eval.runner import anpr_metrics, reid_metrics, run


def test_synthetic_reid_is_separable() -> None:
    queries, gallery = synthetic_reid()
    m = reid_metrics(DeterministicEncoder(), queries, gallery)
    # distinct synthetic identities must be perfectly retrievable at rank-1
    assert m["rank1"] == 1.0
    assert m["mAP"] == 1.0
    assert m["n_queries"] == 6


def test_anpr_fixture_exact_match() -> None:
    cases = load_anpr_fixture(DEFAULT_ANPR_FIXTURE)
    m = anpr_metrics(cases)
    # three readable plates recovered + the unreadable one correctly declined
    assert m["exact_match"] == 1.0
    assert m["n_cases"] == 4


def test_run_report_shape() -> None:
    report = run()
    assert set(report) == {"reid", "anpr"}
    assert report["reid"]["rank1"] == 1.0
    assert report["anpr"]["exact_match"] == 1.0


def test_reid_metrics_deterministic() -> None:
    q, g = synthetic_reid()
    enc = DeterministicEncoder()
    assert reid_metrics(enc, q, g) == reid_metrics(enc, q, g)
