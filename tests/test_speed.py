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
        v = est.update(1, _box(cx, 200), now=i * 0.1, ego_delta=(10.0, 0.0))  # ...= the camera's own flow
    assert v is not None and v < 1.0      # ground-relative motion ~ 0


def test_ego_pace_keeping_car_reads_camera_speed() -> None:
    # the reported bug: a car keeping pace with a dashcam is STATIC in the image, but both do
    # ~the same speed. Ego flow at its location is the camera's motion -> it must read that,
    # not "stopped".
    est = SpeedEstimator(meters_per_pixel=0.1, ema_alpha=1.0, window=2.0, min_kmh=3.0)
    v = None
    for i in range(11):
        v = est.update(1, _box(100, 200), now=i * 0.1, ego_delta=(10.0, 0.0))  # car fixed in frame
    # camera flow 10 px/frame @ 10 fps = 100 px/s; 0.1 m/px -> 36 km/h (not 0)
    assert v is not None and abs(v - 36.0) < 1.0


def test_ego_passing_car_true_speed() -> None:
    # camera moves 10 px/frame; a car moves 20 px/frame in the image -> 10 px/frame over ground
    est = SpeedEstimator(meters_per_pixel=0.1, ema_alpha=1.0, window=2.0, min_kmh=0.0)
    v = None
    for i in range(11):
        cx = 100 + i * 20
        v = est.update(1, _box(cx, 200), now=i * 0.1, ego_delta=(10.0, 0.0))
    # ground displacement 10 px/frame @ 10 fps = 100 px/s; 0.1 m/px -> 36 km/h
    assert v is not None and abs(v - 36.0) < 1.0


def test_height_scale_consistent_across_depth() -> None:
    # a car at a fixed real speed reads the SAME km/h whether near (tall box, many px/s) or far
    # (short box, few px/s). This is the fix for the fixed-mpp bug (near read too fast, far too
    # slow). V = 10 m/s; px/s = V * box_h / real_h, so px/frame @10fps = that / 10.
    def run(box_h: float, px_per_frame: float) -> float:
        est = SpeedEstimator(ema_alpha=1.0, window=2.0, min_kmh=0.0, scale_alpha=1.0)
        v = None
        for i in range(11):
            cx = 100 + i * px_per_frame
            v = est.update(1, _box(cx, 300, w=box_h, h=box_h), now=i * 0.1, scale_ref_m=1.5)
        return v
    near = run(200, 10 * 200 / 1.5 / 10)   # tall box, fast pixels
    far = run(40, 10 * 40 / 1.5 / 10)      # short box, slow pixels
    assert abs(near - far) < 2.0           # depth no longer skews the reading
    assert abs(near - 36.0) < 2.0          # ~36 km/h (10 m/s), roughly metric


def test_no_scale_ref_uses_fixed_mpp() -> None:
    est = SpeedEstimator(meters_per_pixel=0.1, ema_alpha=1.0, window=2.0, min_kmh=0.0)
    v = None
    for i in range(11):
        v = est.update(1, _box(100 + i * 10, 200), now=i * 0.1)   # no size ref -> fallback mpp
    assert v is not None and abs(v - 36.0) < 1.0


def test_unreliable_scale_keeps_last() -> None:
    # a box clipped by the frame edge has a wrong (too-short) height; marked unreliable it must
    # NOT recalibrate the scale to a bogus value.
    est = SpeedEstimator(ema_alpha=1.0, window=2.0, min_kmh=0.0, scale_alpha=1.0)
    est.update(1, _box(100, 300, w=100, h=100), now=0.0, scale_ref_m=1.5)   # good scale: mpp=0.015
    v = None
    for i in range(1, 11):
        v = est.update(1, _box(100 + i * 10, 300, w=100, h=20), now=i * 0.1,
                       scale_ref_m=1.5, scale_reliable=False)      # clipped box, ignored for scale
    # 100 px/s * 0.015 * 3.6 = 5.4 km/h; a bogus recalibration (1.5/20) would give ~27
    assert v is not None and abs(v - 5.4) < 1.0


def test_dashcam_pace_keeping_borrows_camera_speed() -> None:
    # The real dashcam bug: a car directly ahead keeping pace sits at the focus of expansion,
    # where there is no local parallax to measure, so ego compensation alone reads it STOPPED.
    # A parked car off to the side DOES show parallax, teaching the camera's own speed; the
    # pace-keeping car should then borrow that instead of reading 0.
    est = SpeedEstimator(ema_alpha=1.0, window=2.0, min_kmh=3.0, scale_alpha=1.0,
                         still_px=25.0, cam_alpha=1.0)
    # track A — parked car off to the side: it slides across the frame at the camera's rate
    # (apparent == ego), so its ground speed is ~0 but it reveals the camera's speed.
    for i in range(11):
        est.update(1, _box(100 + i * 20, 300, w=100, h=100), now=i * 0.1,
                   ego_delta=(20.0, 0.0), scale_ref_m=1.5, cam_moving=True)
    cam = est.camera_speed()
    assert cam > 5.0                       # learned the camera is moving: 200 px/s * 0.015 * 3.6
    # track B — car dead ahead, frozen in frame, no local parallax (ego ~0 at the FOE)
    v = None
    for i in range(11):
        v = est.update(2, _box(500, 300, w=100, h=100), now=i * 0.1,
                       ego_delta=(0.0, 0.0), scale_ref_m=1.5, cam_moving=True)
    assert v is not None and abs(v - cam) < 1.0     # borrows the camera speed, not "stopped"
    # track A itself (parked) still reads ~0 over the ground
    a = est.get(1)
    assert a is not None and a < 3.0


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
