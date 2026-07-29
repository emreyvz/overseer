from trajectory.intent import IntentEstimator


def _box(cx, cy, w=30, h=60):
    return (cx - w / 2, cy - h, cx + w / 2, cy)   # bottom-center = (cx, cy)


def test_none_until_enough_motion() -> None:
    est = IntentEstimator()
    assert est.update(1, _box(100, 200), now=0.0, frame_diag=1000) is None


def test_stationary_reads_loitering() -> None:
    est = IntentEstimator(loiter_seconds=4.0)
    it = None
    for i in range(20):
        it = est.update(1, _box(100, 200), now=i * 0.5, frame_diag=1000)   # 10s in place
    assert it is not None and it["intent"] in ("loitering", "waiting")
    assert it["confidence"] > 0.3 and "stationary" in it["why"]


def test_direct_movement_reads_transiting() -> None:
    est = IntentEstimator()
    it = None
    for i in range(20):
        it = est.update(1, _box(100 + i * 40, 200), now=i * 0.3, frame_diag=1000)  # straight line
    assert it is not None and it["intent"] in ("transiting", "hurrying")
    assert it["confidence"] > 0.3


def test_back_and_forth_reads_pacing_or_searching() -> None:
    est = IntentEstimator()
    it = None
    for i in range(24):
        cx = 200 + (80 if i % 2 else -80)      # oscillate: lots of path, little net displacement
        it = est.update(1, _box(cx, 200), now=i * 0.3, frame_diag=1000)
    assert it is not None and it["intent"] in ("pacing", "searching", "monitoring surroundings")


def test_prune_and_reset() -> None:
    est = IntentEstimator()
    est.update(1, _box(100, 200), now=0.0, frame_diag=1000)
    est.prune(now=100.0)
    est.update(2, _box(0, 0), now=0.0, frame_diag=1000)
    est.reset()
    assert est.update(2, _box(0, 0), now=0.1, frame_diag=1000) is None
