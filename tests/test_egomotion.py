import cv2
import numpy as np

from vision.egomotion import EgoMotion


def _textured(w=640, h=480):
    rng = np.random.RandomState(0)          # deterministic
    img = rng.randint(0, 255, (h, w, 3), dtype=np.uint8)
    for i in range(0, w, 40):               # add strong edges so LK has features to track
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
    dx, dy, moving = ego.update(_textured())
    assert (dx, dy, moving) == (0.0, 0.0, False)


def test_static_camera_reads_still() -> None:
    ego = EgoMotion()
    frame = _textured()
    ego.update(frame)
    dx, dy, moving = ego.update(frame)      # identical frame -> no camera motion
    assert not moving and abs(dx) < 1.0 and abs(dy) < 1.0


def test_panning_camera_detected_and_measured() -> None:
    ego = EgoMotion()
    frame = _textured()
    ego.update(frame)
    dx = moving = None
    for _ in range(3):                      # a steady rightward pan of 12 full-frame px/frame
        frame = _shift(frame, 12, 0)
        dx, dy, moving = ego.update(frame)
    assert moving is True
    assert abs(abs(dx) - 12.0) < 5.0        # recovers roughly the induced shift


def test_reset_clears_history() -> None:
    ego = EgoMotion()
    ego.update(_textured())
    ego.reset()
    dx, dy, moving = ego.update(_textured())   # first frame again after reset
    assert (dx, dy, moving) == (0.0, 0.0, False)
