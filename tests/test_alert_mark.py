"""Incident-marker geometry for the annotated replay (where-to-look overlay). Verifies
the triggering bbox and breached-zone polygon are normalized against the frame, without
constructing a full Backend (methods are exercised against a light stub)."""
from types import SimpleNamespace

import numpy as np
import pytest

from server.backend import Backend


class _Zones:
    def __init__(self, views):
        self._views = views

    def snapshot(self):
        return self._views


class _Stub:
    """Just enough of Backend for _alert_mark. _frame_dims mirrors the real one."""
    def __init__(self, w, h, zone_views=None):
        self._latest_img = np.zeros((h, w, 3), np.uint8)
        self.zones = _Zones(zone_views or [])

    def _frame_dims(self):
        h, w = self._latest_img.shape[:2]
        return (w, h)


def test_bbox_normalized_against_frame():
    s = _Stub(1920, 1080)
    m = Backend._alert_mark(s, (192, 108, 576, 324), "weapon", "weapon")
    assert m["kind"] == "weapon"
    assert m["label"] == "WEAPON"                       # upper-cased
    # x=192/1920=.1, y=108/1080=.1, w=384/1920=.2, h=216/1080=.2
    assert m["bbox"] == pytest.approx([0.1, 0.1, 0.2, 0.2])


def test_zone_polygon_normalized():
    zv = SimpleNamespace(zone_id=3, polygon=[(192, 108), (960, 540), (1728, 972)])
    s = _Stub(1920, 1080, [zv])
    m = Backend._alert_mark(s, None, "person", "restricted", zone_id=3)
    assert m["zone"] == [[0.1, 0.1], [0.5, 0.5], [0.9, 0.9]]
    assert "bbox" not in m                              # no object bbox given


def test_bbox_and_zone_together():
    zv = SimpleNamespace(zone_id=7, polygon=[(0, 0), (1920, 1080)])
    s = _Stub(1920, 1080, [zv])
    m = Backend._alert_mark(s, (0, 0, 960, 540), "person", "line cross", zone_id=7)
    assert m["bbox"] == pytest.approx([0.0, 0.0, 0.5, 0.5])
    assert m["zone"] == [[0.0, 0.0], [1.0, 1.0]]


def test_returns_none_with_nothing_to_mark():
    s = _Stub(1920, 1080)
    assert Backend._alert_mark(s, None, "person", "x") is None
    # unknown zone id → still nothing
    assert Backend._alert_mark(s, None, "person", "x", zone_id=99) is None
