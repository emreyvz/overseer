import numpy as np

from server.live_make import LiveMakeReader


class _StubClf:
    """Deterministic stand-in for MakeClassifier."""
    def __init__(self, answers):
        self._answers = list(answers)
        self.calls = 0

    def classify(self, crop):
        self.calls += 1
        return self._answers.pop(0) if self._answers else None


def _crop(w=80, h=80):
    return np.zeros((h, w, 3), dtype=np.uint8)


def _reader(answers, **kw):
    r = LiveMakeReader(_StubClf(answers), **kw)
    r.stop()                 # kill the bg thread so process_one() is driven synchronously
    return r


def test_offer_throttle_then_classify() -> None:
    r = _reader([("Renault", 0.9)], interval=4.0)
    r.offer("t1", _crop(), now=0.0)
    r.offer("t1", _crop(), now=1.0)     # within interval -> throttled, not queued
    assert r.process_one() is True
    assert r.process_one() is False     # only one crop was queued
    assert r.make_for("t1") == "Renault"


def test_skips_tiny_crops() -> None:
    r = _reader([("BMW", 0.9)], min_w=64, min_h=48)
    r.offer("t1", _crop(30, 30), now=0.0)   # below min size -> never queued
    assert r.process_one() is False
    assert r.make_for("t1") is None


def test_uncertain_leaves_no_make() -> None:
    r = _reader([None], interval=0.0)
    r.offer("t1", _crop(), now=0.0)
    r.process_one()
    assert r.make_for("t1") is None


def test_make_for_unknown_track() -> None:
    r = _reader([])
    assert r.make_for("ghost") is None


def test_prune_and_reset() -> None:
    r = _reader([("Audi", 0.9), ("Fiat", 0.9)], interval=0.0)
    r.offer("t1", _crop(), now=0.0)
    r.process_one()
    assert r.make_for("t1") == "Audi"
    r.prune({"other"})
    assert r.make_for("t1") is None
    r.offer("t2", _crop(), now=0.0)
    r.process_one()
    assert r.make_for("t2") == "Fiat"
    r.reset()
    assert r.make_for("t2") is None
