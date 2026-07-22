import numpy as np

from forensic.palette import dominant_color_name


def _patch(bgr: tuple[int, int, int]) -> np.ndarray:
    img = np.zeros((20, 20, 3), dtype=np.uint8)
    img[:] = bgr
    return img


def test_named_solid_colors() -> None:
    assert dominant_color_name(_patch((0, 0, 255))) == "red"
    assert dominant_color_name(_patch((0, 255, 0))) == "green"
    assert dominant_color_name(_patch((255, 0, 0))) == "blue"


def test_achromatic() -> None:
    assert dominant_color_name(_patch((0, 0, 0))) == "black"
    assert dominant_color_name(_patch((128, 128, 128))) == "gray"
    assert dominant_color_name(_patch((255, 255, 255))) == "white"


def test_empty_crop() -> None:
    assert dominant_color_name(np.zeros((0, 0, 3), dtype=np.uint8)) == "unknown"
