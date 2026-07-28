import numpy as np

from vehicle.make import MakeClassifier, _pick

LABELS = ["Audi", "BMW", "Renault", "Fiat"]


def test_pick_confident_and_decisive() -> None:
    probs = np.array([0.7, 0.1, 0.1, 0.1], dtype=np.float32)
    assert _pick(probs, LABELS, min_conf=0.35, min_margin=0.1) == ("Audi", 0.7)


def test_pick_rejects_low_confidence() -> None:
    probs = np.array([0.30, 0.28, 0.22, 0.20], dtype=np.float32)   # top < min_conf
    assert _pick(probs, LABELS, min_conf=0.35, min_margin=0.05) is None


def test_pick_rejects_ambiguous_margin() -> None:
    probs = np.array([0.40, 0.38, 0.12, 0.10], dtype=np.float32)   # decisive? no (0.02 margin)
    assert _pick(probs, LABELS, min_conf=0.35, min_margin=0.10) is None


def test_pick_size_mismatch_is_none() -> None:
    assert _pick(np.array([0.9, 0.1], dtype=np.float32), LABELS, 0.3, 0.1) is None
    assert _pick(np.empty(0, dtype=np.float32), LABELS, 0.3, 0.1) is None


def test_unavailable_when_weights_missing(tmp_path) -> None:
    clf = MakeClassifier(tmp_path / "nope.torchscript")
    assert clf.available() is False
    assert clf.classify(np.zeros((64, 64, 3), dtype=np.uint8)) is None


class _StubMake(MakeClassifier):
    """Exercises make_for's throttle/cache without loading a real model."""
    def __init__(self, answers, **kw):
        super().__init__("x.torchscript", **kw)
        self._answers = list(answers)
        self.calls = 0

    def classify(self, crop):   # deterministic stand-in for the real ViT
        self.calls += 1
        return self._answers.pop(0) if self._answers else None


def _crop():
    return np.zeros((80, 80, 3), dtype=np.uint8)


def test_make_for_throttles_within_interval() -> None:
    clf = _StubMake([("Renault", 0.9)], interval=4.0)
    assert clf.make_for(1, _crop(), now=0.0) == "Renault"
    assert clf.make_for(1, _crop(), now=1.0) == "Renault"   # cached, no reclassify
    assert clf.calls == 1


def test_make_for_reclassifies_after_interval() -> None:
    clf = _StubMake([("Renault", 0.9), ("Fiat", 0.8)], interval=4.0)
    assert clf.make_for(1, _crop(), now=0.0) == "Renault"
    assert clf.make_for(1, _crop(), now=5.0) == "Fiat"      # interval elapsed -> reclassify
    assert clf.calls == 2


def test_make_for_keeps_last_good_on_uncertain() -> None:
    clf = _StubMake([("BMW", 0.9), None], interval=0.0)     # 2nd frame uncertain
    assert clf.make_for(2, _crop(), now=0.0) == "BMW"
    assert clf.make_for(2, _crop(), now=1.0) == "BMW"       # keeps the last confident label


def test_prune_and_reset() -> None:
    clf = _StubMake([("Audi", 0.9)], interval=10.0)
    clf.make_for(7, _crop(), now=0.0)
    clf.prune({999})
    assert 7 not in clf._cache
    clf.make_for(8, _crop(), now=0.0)
    clf.reset()
    assert clf._cache == {}
