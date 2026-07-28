from trajectory.speed import SpeedEstimator


def _box(cx: float, cy: float, w: float = 40, h: float = 40):
    return (cx - w / 2, cy - h, cx + w / 2, cy)   # bottom-center is (cx, cy)


def test_none_until_two_samples() -> None:
    est = SpeedEstimator(meters_per_pixel=0.1)
    assert est.update(1, _box(0, 100), now=0.0) is None


def test_stationary_reads_zero() -> None:
    est = SpeedEstimator(meters_per_pixel=0.1, min_kmh=3.0)
    est.update(1, _box(100, 200), now=0.0)
    v = 1.0
    for i in range(1, 6):
        v = est.update(1, _box(100, 200), now=float(i))
    assert v == 0.0


def test_moving_faster_reads_higher() -> None:
    # same camera scale: a fast track must read higher km/h than a slow one
    slow = SpeedEstimator(meters_per_pixel=0.1, ema_alpha=1.0, min_kmh=0.0)
    fast = SpeedEstimator(meters_per_pixel=0.1, ema_alpha=1.0, min_kmh=0.0)
    for i in range(5):
        s = slow.update(1, _box(100 + i * 10, 200), now=float(i) * 0.2)   # 10 px / 0.2s
        f = fast.update(1, _box(100 + i * 40, 200), now=float(i) * 0.2)   # 40 px / 0.2s
    assert f > s > 0


def test_kmh_matches_calibration() -> None:
    # 10 px/frame at 10 fps = 100 px/s; 0.1 m/px -> 10 m/s -> 36 km/h
    est = SpeedEstimator(meters_per_pixel=0.1, ema_alpha=1.0, window=2.0, min_kmh=0.0)
    v = None
    for i in range(11):
        v = est.update(1, _box(100 + i * 10, 200), now=i * 0.1)
    assert v is not None and abs(v - 36.0) < 1.0


def test_ego_parked_car_reads_zero() -> None:
    # camera pans so a PARKED car slides across the frame at the camera's own rate -> ground 0
    est = SpeedEstimator(meters_per_pixel=0.1, ema_alpha=1.0, window=2.0, min_kmh=0.0)
    v = None
    for i in range(11):
        cx = 100 + i * 10                 # car appears to move 10 px/frame...
        ego = (i * 10.0, 0.0)             # ...exactly the camera's cumulative shift
        v = est.update(1, _box(cx, 200), now=i * 0.1, ego=ego)
    assert v is not None and v < 1.0      # ground-relative motion ~ 0


def test_ego_passing_car_true_speed() -> None:
    # camera moves 10 px/frame; a car moves 20 px/frame in the image -> 10 px/frame over ground
    est = SpeedEstimator(meters_per_pixel=0.1, ema_alpha=1.0, window=2.0, min_kmh=0.0)
    v = None
    for i in range(11):
        cx = 100 + i * 20
        ego = (i * 10.0, 0.0)
        v = est.update(1, _box(cx, 200), now=i * 0.1, ego=ego)
    # ground displacement 10 px/frame @ 10 fps = 100 px/s; 0.1 m/px -> 36 km/h
    assert v is not None and abs(v - 36.0) < 1.0


def test_teleport_ignored() -> None:
    est = SpeedEstimator(meters_per_pixel=1.0, max_kmh=200.0, ema_alpha=1.0, min_kmh=0.0)
    est.update(1, _box(0, 200), now=0.0)
    est.update(1, _box(30, 200), now=0.1)          # sane reading establishes ema
    before = est.get(1)
    v = est.update(1, _box(99999, 200), now=0.2)    # absurd jump -> keep prior ema
    assert v == before


def test_prune_and_reset() -> None:
    est = SpeedEstimator()
    est.update(1, _box(0, 200), now=0.0)
    est.update(1, _box(10, 200), now=0.1)
    est.prune(now=100.0)
    assert est.get(1) is None
    est.update(2, _box(0, 200), now=0.0)
    est.reset()
    assert est.get(2) is None
