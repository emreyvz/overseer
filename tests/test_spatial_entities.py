# tests/test_spatial_entities.py
"""The 3D SPATIAL view turns each detection into a scene entity. These verify the entity carries the
normalized apparent size (sw/sh) the frontend needs to scale a real 3D primitive (car / body) at the
detection's depth, plus the normalized centre and completion boxes — without spinning up a full
Backend (we bypass __init__ and inject a fake detector)."""
import numpy as np

from server.backend import Backend


class _Det:
    """Minimal stand-in for a detector Detection (only the fields _spatial_entities reads)."""

    def __init__(self, bbox, category, confidence=0.8, label=None):
        self.bbox = bbox
        self.category = category
        self.confidence = confidence
        self.label = label or category


class _FakeDetector:
    def __init__(self, dets):
        self._dets = dets

    def detect_crop(self, frame, conf=0.3):  # noqa: ARG002
        return self._dets


class _FakeConfig:
    def get(self, key, default=None):  # noqa: ARG002
        return default


def _backend_with(dets):
    b = Backend.__new__(Backend)   # bypass heavy __init__
    b._yolo = _FakeDetector(dets)
    b._roster_det = None
    b.config = _FakeConfig()
    return b


def test_spatial_entities_include_normalized_size() -> None:
    frame = np.zeros((100, 200, 3), np.uint8)          # h=100, w=200
    disp = np.full((100, 200), 0.5, np.float32)
    dets = [_Det((20, 30, 60, 80), "vehicle", 0.9, "CAR")]
    ents, boxes = _backend_with(dets)._spatial_entities(frame, disp, 0.0, 1.0)

    assert len(ents) == 1 and len(boxes) == 1
    e = ents[0]
    assert e["cls"] == "vehicle"
    # apparent size (normalized) -> lets the frontend size the 3D car: 40/200, 50/100
    assert abs(e["sw"] - 0.2) < 1e-3
    assert abs(e["sh"] - 0.5) < 1e-3
    # normalized centre of the box
    assert abs(e["cx"] - 0.2) < 1e-3          # (20+60)/2/200
    assert abs(e["cy"] - 0.55) < 1e-3         # (30+80)/2/100
    # a finite depth sample in [0,1]
    assert 0.0 <= e["depth"] <= 1.0
    # completion box is normalized xyxy
    assert len(boxes[0]) == 4 and all(0.0 <= v <= 1.0 for v in boxes[0])


def test_spatial_entities_size_is_finite_for_edge_boxes() -> None:
    frame = np.zeros((90, 160, 3), np.uint8)
    disp = np.full((90, 160), 0.4, np.float32)
    dets = [_Det((0, 0, 160, 90), "person")]        # full-frame box
    ents, _ = _backend_with(dets)._spatial_entities(frame, disp, 0.0, 1.0)
    assert abs(ents[0]["sw"] - 1.0) < 1e-3 and abs(ents[0]["sh"] - 1.0) < 1e-3


def test_spatial_entities_empty_without_detector() -> None:
    b = Backend.__new__(Backend)
    b._yolo = None
    b._roster_det = None
    b.config = _FakeConfig()
    ents, boxes = b._spatial_entities(np.zeros((10, 10, 3), np.uint8),
                                      np.zeros((10, 10), np.float32), 0.0, 1.0)
    assert ents == [] and boxes == []
