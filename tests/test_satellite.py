from pathlib import Path

import cv2
import numpy as np
import pytest

from analyzers.satellite import SatelliteAnalyzer, _Track
from camera.frame_buffer import Frame
from core.config import Config, load_config
from events.types import EventType
from plugins.analyzer import EnvironmentContext
from vision.metrics import ImageMetrics


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "analyzers:\n  satellite:\n    enabled: true\n    diff_threshold: 30\n"
        "    min_track_length: 6\n    min_speed: 0.5\n    max_speed: 8.0\n"
        "    max_direction_std_deg: 15.0\n",
        encoding="utf-8",
    )
    return load_config(p)


def _ctx(is_day: bool | None) -> EnvironmentContext:
    return EnvironmentContext(
        metrics=ImageMetrics(brightness=10.0, contrast=0.0, sharpness=0.0, noise=0.0),
        is_day=is_day,
    )


def point_frame(x: int, y: int) -> np.ndarray:
    img = np.zeros((240, 320, 3), dtype=np.uint8)
    cv2.circle(img, (x, y), 2, (255, 255, 255), -1)  # small bright point
    return img


def frame(image: np.ndarray, i: int) -> Frame:
    return Frame(image=image, timestamp=float(i), seq=i)


def test_slow_straight_point_is_satellite(config: Config) -> None:
    a = SatelliteAnalyzer(config)
    detected = False
    for i in range(12):
        # slow linear motion: +3px x, +1px y each frame (small at 320-wide scale)
        reading = a.analyze(frame(point_frame(60 + 3 * i, 60 + i), i), _ctx(False))
        if reading.event is not None:
            detected = True
            assert reading.event.label == "Satellite"
            assert reading.event.event_type is EventType.SATELLITE
    assert detected


def test_erratic_point_not_satellite(config: Config) -> None:
    a = SatelliteAnalyzer(config)
    rng = np.random.default_rng(3)
    fired = False
    for i in range(12):
        x = int(rng.integers(40, 280))
        y = int(rng.integers(40, 200))
        if a.analyze(frame(point_frame(x, y), i), _ctx(False)).event is not None:
            fired = True
    assert not fired  # random jumps -> no straight slow track


def test_day_skips(config: Config) -> None:
    a = SatelliteAnalyzer(config)
    fired = False
    for i in range(12):
        if a.analyze(frame(point_frame(60 + 3 * i, 60 + i), i), _ctx(True)).event:
            fired = True
    assert not fired


def test_fast_point_not_satellite(config: Config) -> None:
    a = SatelliteAnalyzer(config)
    fired = False
    for i in range(12):
        # 30px/frame -> exceeds max_speed and breaks nearest-neighbor gating
        if a.analyze(frame(point_frame(20 + 30 * i, 60), i), _ctx(False)).event:
            fired = True
    assert not fired


def test_reset_clears_tracks(config: Config) -> None:
    a = SatelliteAnalyzer(config)
    for i in range(4):
        a.analyze(frame(point_frame(60 + 3 * i, 60 + i), i), _ctx(False))
    a.reset()
    assert a._tracks == []  # candidate tracks from the old source are cleared


def blank_frame(i: int) -> Frame:
    return Frame(image=np.zeros((240, 320, 3), dtype=np.uint8), timestamp=float(i), seq=i)


def test_new_track_not_aged_in_creation_frame(config: Config) -> None:
    a = SatelliteAnalyzer(config)
    # Frame 0: create a single track
    a.analyze(frame(point_frame(60, 60), 0), _ctx(False))
    assert len(a._tracks) == 1
    assert a._tracks[0].missed == 0
    # Frames 1-3: feed blank frames (no bright points)
    # Without the fix, missed goes 0→1→2→3→4 (pruned after frame 3)
    # With the fix, missed goes 0→1→2→3 (not pruned, stays <= 3)
    for i in range(1, 4):
        a.analyze(blank_frame(i), _ctx(False))
    assert len(a._tracks) >= 1  # track should survive


def test_static_point_history_is_bounded(config: Config) -> None:
    a = SatelliteAnalyzer(config)
    fired = False
    # A static bright night point (streetlight, moon, lit window, IR hotspot) is
    # re-detected at the SAME location every frame: it never goes stale (missed
    # stays 0) and, before the fix, its points list grows without bound.
    for i in range(30):
        if a.analyze(frame(point_frame(120, 100), i), _ctx(False)).event is not None:
            fired = True
    assert not fired  # static -> speed ~0 < min_speed, never confirms as satellite
    assert len(a._tracks) == 1
    # Teeth: without the cap, len(points) would be ~30 (one per frame fed).
    assert len(a._tracks[0].points) <= a._history_cap


def test_is_satellite_rejects_in_band_speed_but_crooked(config: Config) -> None:
    a = SatelliteAnalyzer(config)
    # Per-step speed is in-band (~3px on the axis steps, ~4.2px on diagonals stay
    # under max_speed=8.0), but direction alternates 90 degrees each step, so
    # the track is not straight.
    track = _Track((0.0, 0.0))
    track.points = [
        (0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (6.0, 3.0),
        (6.0, 6.0), (9.0, 6.0), (9.0, 9.0),
    ]
    assert len(track.points) >= a._min_len
    assert a._is_satellite(track) is False  # direction std too high


def test_is_satellite_rejects_straight_but_too_fast(config: Config) -> None:
    a = SatelliteAnalyzer(config)
    # Perfectly straight line, but per-step speed (20px) exceeds max_speed (8.0).
    track = _Track((0.0, 0.0))
    track.points = [(20.0 * i, 0.0) for i in range(7)]
    assert len(track.points) >= a._min_len
    assert a._is_satellite(track) is False  # speed out of band


def test_is_satellite_accepts_straight_in_band_track(config: Config) -> None:
    a = SatelliteAnalyzer(config)
    # Straight line, per-step speed (3px) within [min_speed, max_speed] band.
    # Proves the rejection tests above aren't vacuously always-False.
    track = _Track((0.0, 0.0))
    track.points = [(3.0 * i, 0.0) for i in range(7)]
    assert len(track.points) >= a._min_len
    assert a._is_satellite(track) is True
