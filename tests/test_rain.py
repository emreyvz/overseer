from pathlib import Path

import cv2
import numpy as np
import pytest

from analyzers.rain import RainAnalyzer
from camera.frame_buffer import Frame
from core.config import Config, load_config
from plugins.analyzer import EnvironmentContext


def cfg(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "analyzers:\n  rain:\n    enabled: true\n    diff_threshold: 25\n"
        "    onset: 0.5\n    clear: 0.3\n    min_onset_seconds: 1.0\n",
        encoding="utf-8",
    )
    return load_config(p)


def ctx() -> EnvironmentContext:
    from vision.metrics import ImageMetrics
    return EnvironmentContext(
        metrics=ImageMetrics(brightness=100.0, contrast=40.0, sharpness=200.0, noise=1.0)
    )


def static_frame(i: int) -> Frame:
    base = np.full((240, 320, 3), 40, dtype=np.uint8)
    rng = np.random.default_rng(5000 + i)
    ys = rng.integers(0, 240, size=800)
    xs = rng.integers(0, 320, size=800)
    base[ys, xs] = 255  # ~800 scattered bright pixels, positions change each frame
    return Frame(image=base, timestamp=float(i), seq=i)


def rain_frame(i: int) -> Frame:
    rng = np.random.default_rng(7)
    base = rng.integers(0, 120, (240, 320, 3), dtype=np.uint8).copy()
    # Vertical bright streaks at moving x positions (rain-like).
    rain_rng = np.random.default_rng(1000 + i)
    for _ in range(60):
        x = int(rain_rng.integers(0, 320))
        y = int(rain_rng.integers(0, 220))
        base[y:y + 18, x:x + 1] = 255
    return Frame(image=base, timestamp=float(i), seq=i)


def sparse_noise_frame(i: int) -> Frame:
    base = np.full((240, 320, 3), 60, dtype=np.uint8)
    rng = np.random.default_rng(9000 + i)
    for _ in range(12):
        x = int(rng.integers(0, 320))
        y = int(rng.integers(0, 240))
        base[y, x] = 255
    return Frame(image=base, timestamp=float(i), seq=i)


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    return cfg(tmp_path)


def test_static_scene_low_rain(config: Config) -> None:
    a = RainAnalyzer(config)
    prob = 0.0
    for i in range(15):
        prob = a.analyze(static_frame(i), ctx()).values["rain_probability"]
    assert prob < 0.2


def test_rain_scene_higher_than_static(config: Config) -> None:
    # Fixture-sanity: prove the static baseline genuinely crosses diff_threshold
    # between consecutive frames, so the total>0 scoring branch actually runs
    # and this test can never silently regress to a no-op comparison.
    g0 = cv2.cvtColor(static_frame(0).image, cv2.COLOR_BGR2GRAY)
    g1 = cv2.cvtColor(static_frame(1).image, cv2.COLOR_BGR2GRAY)
    assert int(np.count_nonzero(cv2.absdiff(g1, g0) > 25)) > 0

    rainy = RainAnalyzer(config)
    static = RainAnalyzer(config)
    rp = sp = 0.0
    for i in range(15):
        rp = rainy.analyze(rain_frame(i), ctx()).values["rain_probability"]
        sp = static.analyze(static_frame(i), ctx()).values["rain_probability"]
    assert rp > sp
    assert sp < 0.2
    assert rp > 0.2


def test_first_frame_zero(config: Config) -> None:
    a = RainAnalyzer(config)
    assert a.analyze(rain_frame(0), ctx()).values["rain_probability"] == 0.0


def test_sparse_supra_threshold_noise_stays_low(config: Config) -> None:
    a = RainAnalyzer(config)
    prob = 0.0
    for i in range(15):
        prob = a.analyze(sparse_noise_frame(i), ctx()).values["rain_probability"]
    assert prob < 0.2


def test_band_weight_tapers() -> None:
    from analyzers.rain import _band_weight
    assert _band_weight(0.0) == 0.0            # zero motion -> zero weight
    assert abs(_band_weight(0.0025) - 0.5) < 1e-9   # low taper midpoint
    assert _band_weight(0.005) == 1.0          # band lower edge
    assert _band_weight(0.1) == 1.0            # inside band
    assert _band_weight(0.2) == 1.0            # band upper edge
    assert abs(_band_weight(0.3) - 0.5) < 1e-9      # high taper midpoint
    assert _band_weight(0.4) == 0.0            # high taper end
    assert _band_weight(0.6) == 0.0            # beyond -> clamped zero


def test_rain_onset_event(config: Config) -> None:
    a = RainAnalyzer(config)
    event = None
    for i in range(20):
        r = a.analyze(rain_frame(i), ctx())
        if r.event is not None:
            event = r.event
            break
    assert event is not None and event.label == "Rain started"
