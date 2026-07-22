import numpy as np

from camera.frame_buffer import Frame
from core.pipeline import AnalysisResult, _zone_inputs
from plugins.base import Detection
from vision.metrics import ImageMetrics


def test_zone_inputs_filters() -> None:
    dets = {
        "person": [Detection("person", 0.9, (0, 0, 10, 10), "person", track_id=3),
                   Detection("person", 0.9, (0, 0, 10, 10), "person", track_id=None)],
        "vehicle": [Detection("araba", 0.9, (0, 0, 10, 10), "vehicle", track_id=8)],
        "animal": [Detection("kedi", 0.9, (0, 0, 10, 10), "animal", track_id=5)],
    }
    got = _zone_inputs(dets)
    assert sorted(d.track_id for d in got) == [3, 8]  # person+vehicle w/ track_id


def test_analysis_result_zones_default() -> None:
    r = AnalysisResult(
        frame=Frame(image=np.zeros((4, 4, 3), np.uint8), timestamp=0.0, seq=0),
        detections={}, metrics=ImageMetrics(0, 0, 0, 0), motion_percent=0.0,
        fps=0.0, inference_ms=0.0)
    assert r.zones == []
