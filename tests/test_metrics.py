import cv2
import numpy as np

from vision.metrics import ImageMetrics, compute_metrics


def test_dark_vs_bright() -> None:
    dark = np.full((64, 64, 3), 10, dtype=np.uint8)
    bright = np.full((64, 64, 3), 240, dtype=np.uint8)
    assert compute_metrics(dark).brightness < 20
    assert compute_metrics(bright).brightness > 230


def test_flat_image_low_contrast_and_sharpness() -> None:
    flat = np.full((64, 64, 3), 128, dtype=np.uint8)
    m = compute_metrics(flat)
    assert m.contrast < 1.0
    assert m.sharpness < 1.0


def test_checkerboard_high_contrast_and_sharpness() -> None:
    tile = np.array([[0, 255], [255, 0]], dtype=np.uint8)
    board = np.tile(tile, (32, 32))
    image = cv2.cvtColor(board, cv2.COLOR_GRAY2BGR)
    m = compute_metrics(image)
    assert m.contrast > 100
    assert m.sharpness > 1000


def test_noisy_image_higher_noise_metric() -> None:
    rng = np.random.default_rng(1)
    clean = np.full((64, 64, 3), 128, dtype=np.uint8)
    noisy = np.clip(
        clean.astype(np.int16) + rng.integers(-60, 60, clean.shape), 0, 255
    ).astype(np.uint8)
    assert compute_metrics(noisy).noise > compute_metrics(clean).noise + 5


def test_returns_dataclass() -> None:
    m = compute_metrics(np.zeros((32, 32, 3), dtype=np.uint8))
    assert isinstance(m, ImageMetrics)
