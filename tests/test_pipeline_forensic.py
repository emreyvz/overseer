import numpy as np

from camera.frame_buffer import Frame
from core.pipeline import AnalysisResult
from plugins.base import Detection


class _FacadeSpy:
    def __init__(self) -> None:
        self.calls = []

    def offer(self, source_id, frame, persons, accessories):
        self.calls.append((source_id, [p.track_id for p in persons],
                           [a.label for a in accessories]))
        return ["view"]


def test_split_persons_and_accessories() -> None:
    from core.pipeline import _forensic_inputs

    dets = {
        "person": [Detection("person", 0.9, (0, 0, 10, 10), "person", track_id=3),
                   Detection("person", 0.9, (0, 0, 10, 10), "person", track_id=None)],
        "accessory": [Detection("backpack", 0.8, (1, 1, 5, 5), "accessory")],
    }
    persons, accessories = _forensic_inputs(dets)
    assert [p.track_id for p in persons] == [3]   # track_id=None dropped
    assert [a.label for a in accessories] == ["backpack"]


def test_analysis_result_has_tracklets_default() -> None:
    frame = Frame(image=np.zeros((4, 4, 3), np.uint8), timestamp=0.0, seq=0)
    from vision.metrics import ImageMetrics
    r = AnalysisResult(frame=frame, detections={}, metrics=ImageMetrics(0, 0, 0, 0),
                       motion_percent=0.0, fps=0.0, inference_ms=0.0)
    assert r.tracklets == []
