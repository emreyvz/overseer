import numpy as np

from forensic.attributes import (
    AttributeSet, ClassicalAttributes, associate_accessories,
)
from plugins.base import Detection


def _person_crop() -> np.ndarray:
    crop = np.zeros((100, 40, 3), dtype=np.uint8)
    crop[:50] = (0, 0, 255)   # upper half red (BGR)
    crop[50:] = (255, 0, 0)   # lower half blue
    return crop


def test_extract_colors_and_bands() -> None:
    crop = _person_crop()
    attrs = ClassicalAttributes().extract(crop, bbox=(10, 10, 50, 110), frame_hw=(200, 300))
    assert isinstance(attrs, AttributeSet)
    assert attrs.upper_color == "red"
    assert attrs.lower_color == "blue"
    # bbox height 100 / frame 200 = 0.5 -> medium
    assert attrs.height_band == "medium"
    assert attrs.build in {"slim", "medium", "broad"}


def test_height_bands() -> None:
    ca = ClassicalAttributes()
    crop = _person_crop()
    assert ca.extract(crop, (0, 0, 40, 50), (300, 300)).height_band == "short"    # 50/300
    assert ca.extract(crop, (0, 0, 40, 250), (300, 300)).height_band == "tall"    # 250/300


def _acc(label: str, bbox: tuple[int, int, int, int]) -> Detection:
    return Detection(label=label, confidence=0.9, bbox=bbox, category="accessory")


def test_associate_accessories() -> None:
    person = (100, 100, 200, 400)
    inside = _acc("backpack", (120, 110, 180, 200))     # inside the person
    outside = _acc("umbrella", (500, 500, 560, 560))         # uzak
    names = associate_accessories(person, [inside, outside], iou_thresh=0.5)
    assert names == ["backpack"]


def test_accessories_deduped() -> None:
    person = (0, 0, 100, 300)
    a = _acc("bag", (10, 10, 40, 60))
    b = _acc("bag", (20, 20, 50, 70))
    assert associate_accessories(person, [a, b], iou_thresh=0.5) == ["bag"]
