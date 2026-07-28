import cv2
import numpy as np

from vision.egomotion import EgoMotion


def _textured(w=640, h=480):
    rng = np.random.RandomState(0)          # deterministic
    img = rng.randint(0, 255, (h, w, 3), dtype=np.uint8)
    for i in range(0, w, 40):               # strong edges so LK has features to track
        cv2.line(img, (i, 0), (i, h), (255, 255, 255), 1)
    for j in range(0, h, 40):
        cv2.line(img, (0, j), (w, j), (255, 255, 255), 1)
    return img


def _shift(img, tx, ty):
    h, w = img.shape[:2]
    m = np.float32([[1, 0, tx], [0, 1, ty]])
    return cv2.warpAffine(img, m, (w, h))


def test_first_frame_is_not_moving() -> None:
    ego = EgoMotion()
    assert ego.update(_textured()) is False
    assert ego.flow_at(320, 240) == (0.0, 0.0)


def test_static_camera_reads_still() -> None:
    ego = EgoMotion()
    frame = _textured()
    ego.update(frame)
    assert ego.update(frame) is False       # identical frame -> no camera motion
    assert ego.flow_at(320, 240) == (0.0, 0.0)


def test_panning_camera_detected_and_modelled() -> None:
    ego = EgoMotion()
    frame = _textured()
    ego.update(frame)
    moving = False
    for _ in range(3):                      # steady rightward pan of 12 full-frame px/frame
        frame = _shift(frame, 12, 0)
        moving = ego.update(frame)
    assert moving is True
    fx, fy = ego.flow_at(320, 240)          # a pure pan -> ~constant flow everywhere
    assert abs(fx - 12.0) < 5.0 and abs(fy) < 5.0


def test_flow_at_varies_with_expansion() -> None:
    # a zoom/forward-motion-like expansion about the centre: flow grows away from centre, so
    # flow_at must differ across the frame (this is what a translation model cannot capture)
    ego = EgoMotion()
    frame = _textured()
    ego.update(frame)
    h, w = frame.shape[:2]
    moving = False
    for _ in range(3):
        frame = cv2.warpAffine(frame, cv2.getRotationMatrix2D((w / 2, h / 2), 0, 1.06), (w, h))
        moving = ego.update(frame)
    assert moving is True
    left = ego.flow_at(w * 0.15, h / 2)[0]
    right = ego.flow_at(w * 0.85, h / 2)[0]
    assert right - left > 3.0               # expansion: flow points outward, right > left


def test_reset_clears_history() -> None:
    ego = EgoMotion()
    ego.update(_textured())
    ego.reset()
    assert ego.update(_textured()) is False     # first frame again after reset
    assert ego.flow_at(10, 10) == (0.0, 0.0)
