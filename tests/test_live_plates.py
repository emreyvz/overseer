import numpy as np

from server.plates import LivePlateReader


class _FakeReader:
    """Injected OCR stub: returns a fixed read for any crop."""
    def __init__(self, reads, available=True):
        self._reads = reads
        self._ok = available
    def available(self):
        return self._ok
    def __call__(self, crop):
        return list(self._reads)


def _crop(w=120, h=40):
    return np.full((h, w, 3), 200, np.uint8)


def test_votes_a_plate_after_enough_reads() -> None:
    lpr = LivePlateReader(reader=_FakeReader([("34 ABC 123", 0.9)]),
                          min_agreement=2, interval=0.0)
    lpr.stop()  # drive synchronously; don't race the background thread
    for _ in range(3):
        lpr.offer("TK.1", _crop(), now=0.0 + _)
        lpr.process_one()
    assert lpr.plate_for("TK.1") == ("34ABC123", 0.9) or lpr.plate_for("TK.1")[0] == "34ABC123"


def test_needs_agreement_no_single_read() -> None:
    lpr = LivePlateReader(reader=_FakeReader([("34ABC123", 0.9)]),
                          min_agreement=3, interval=0.0)
    lpr.stop()
    lpr.offer("TK.1", _crop(), now=0.0)
    lpr.process_one()
    assert lpr.plate_for("TK.1") is None  # one read < min_agreement 3


def test_throttle_skips_rapid_offers() -> None:
    lpr = LivePlateReader(reader=_FakeReader([("34ABC123", 0.9)]), interval=2.5)
    lpr.stop()
    lpr.offer("TK.1", _crop(), now=100.0)
    lpr.offer("TK.1", _crop(), now=100.5)  # within interval -> dropped
    lpr.offer("TK.1", _crop(), now=103.0)  # past interval -> queued
    n = 0
    while lpr.process_one():
        n += 1
    assert n == 2  # only two of the three offers were queued


def test_tiny_crops_skipped() -> None:
    lpr = LivePlateReader(reader=_FakeReader([("X", 0.9)]), min_w=60, min_h=20, interval=0.0)
    lpr.stop()
    lpr.offer("TK.1", _crop(w=30, h=10), now=0.0)  # too small for a plate
    assert lpr.process_one() is False


def test_unavailable_reader_produces_nothing() -> None:
    lpr = LivePlateReader(reader=_FakeReader([("34ABC123", 0.9)], available=False), interval=0.0)
    lpr.stop()
    assert lpr.available() is False
    lpr.offer("TK.1", _crop(), now=0.0)
    lpr.process_one()
    assert lpr.plate_for("TK.1") is None


def test_prune_drops_dead_tracks() -> None:
    lpr = LivePlateReader(reader=_FakeReader([("34ABC123", 0.9)]), min_agreement=1, interval=0.0)
    lpr.stop()
    lpr.offer("TK.1", _crop(), now=0.0)
    lpr.process_one()
    assert lpr.plate_for("TK.1") is not None
    lpr.prune(alive_ids={"TK.2"})
    assert lpr.plate_for("TK.1") is None
