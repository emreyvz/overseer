from match.anpr.voting import PlateVoter


def test_agreement_required() -> None:
    voter = PlateVoter(min_agreement=3)
    # only two agreeing reads -> not asserted
    plate, conf = voter.vote([("34ABC123", 0.9), ("34ABC123", 0.8)])
    assert plate is None
    assert conf == 0.0


def test_consistent_plate_wins() -> None:
    voter = PlateVoter(min_agreement=3)
    reads = [("34ABC123", 0.9), ("34ABC123", 0.8), ("34ABC123", 0.85),
             ("99XYZ", 0.4)]
    plate, conf = voter.vote(reads)
    assert plate == "34ABC123"
    assert abs(conf - (0.9 + 0.8 + 0.85) / 3) < 1e-9


def test_noise_ignored() -> None:
    voter = PlateVoter(min_agreement=2)
    reads = [("", 0.9), ("!!", 0.9), ("AB12", 0.7), ("ab 12", 0.6)]
    plate, conf = voter.vote(reads)
    assert plate == "AB12"


def test_deterministic_tie_break() -> None:
    voter = PlateVoter(min_agreement=2)
    reads = [("AAA", 0.5), ("AAA", 0.5), ("BBB", 0.5), ("BBB", 0.5)]
    # equal total conf & count -> tie broken by plate string ('BBB' > 'AAA')
    assert voter.vote(reads) == voter.vote(list(reversed(reads)))
    assert voter.vote(reads)[0] == "BBB"


def test_empty_reads() -> None:
    assert PlateVoter().vote([]) == (None, 0.0)
