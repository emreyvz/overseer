from pathlib import Path

import cv2
import numpy as np
import pytest

from core.config import Config, load_config
from vision.heatmap import MotionHeatmap


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "heatmap:\n  decay: 0.98\n  width: 120\n  alpha: 0.5\n", encoding="utf-8"
    )
    return load_config(p)


def mask(hot: bool) -> np.ndarray:
    m = np.zeros((90, 120), dtype=np.uint8)
    if hot:
        m[30:60, 40:80] = 255
    return m


def test_accumulates_and_has_data(config: Config) -> None:
    hm = MotionHeatmap(config)
    assert hm.has_data is False
    for _ in range(5):
        hm.accumulate(mask(hot=True))
    assert hm.has_data is True


def test_overlay_changes_hot_region(config: Config) -> None:
    hm = MotionHeatmap(config)
    for _ in range(10):
        hm.accumulate(mask(hot=True))
    frame = np.zeros((90, 120, 3), dtype=np.uint8)
    out = hm.overlay(frame)
    assert out is not frame
    assert (out != 0).any()  # heat drawn somewhere
    # Hot region (center) brighter than a cold corner.
    assert int(out[45, 60].sum()) > int(out[2, 2].sum())


def test_overlay_empty_returns_copy(config: Config) -> None:
    hm = MotionHeatmap(config)
    frame = np.full((40, 40, 3), 7, dtype=np.uint8)
    out = hm.overlay(frame)
    assert out is not frame
    assert (out == frame).all()


def test_export_png(config: Config, tmp_path: Path) -> None:
    hm = MotionHeatmap(config)
    for _ in range(5):
        hm.accumulate(mask(hot=True))
    path = tmp_path / "sub" / "heat.png"
    hm.export_png(path)
    assert path.exists()
    loaded = cv2.imread(str(path))
    assert loaded is not None and loaded.shape[2] == 3


def test_export_png_raises_on_write_failure(
    config: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hm = MotionHeatmap(config)
    for _ in range(5):
        hm.accumulate(mask(hot=True))

    def failing_imwrite(*args: object, **kwargs: object) -> bool:
        return False

    monkeypatch.setattr(cv2, "imwrite", failing_imwrite)
    with pytest.raises(OSError):
        hm.export_png(tmp_path / "heat.png")


def test_reset_clears(config: Config) -> None:
    hm = MotionHeatmap(config)
    hm.accumulate(mask(hot=True))
    hm.reset()
    assert hm.has_data is False
