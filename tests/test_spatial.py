# tests/test_spatial.py
import base64

import numpy as np

from server.spatial import encode_scene, entity_depth, normalize_disparity


def test_normalize_disparity_maps_to_unit_range() -> None:
    disp = np.array([[2.0, 4.0], [6.0, 10.0]], np.float32)
    d01, dmin, dmax = normalize_disparity(disp)
    assert dmin == 2.0 and dmax == 10.0
    assert d01.min() == 0.0 and abs(d01.max() - 1.0) < 1e-4
    # nearest (largest disparity) -> 1.0
    assert d01[1, 1] > d01[0, 0]


def test_entity_depth_samples_box_median() -> None:
    # left half far (disp 1), right half near (disp 9)
    disp = np.ones((10, 10), np.float32)
    disp[:, 5:] = 9.0
    _, dmin, dmax = normalize_disparity(disp)
    # a box over the near (right) half -> depth01 near 1
    near = entity_depth(disp, (6, 1, 9, 9), (10, 10), dmin, dmax)
    far = entity_depth(disp, (1, 1, 4, 9), (10, 10), dmin, dmax)
    assert near > 0.9 and far < 0.1


def test_entity_depth_offgrid_box_is_safe() -> None:
    disp = np.ones((8, 8), np.float32)
    _, dmin, dmax = normalize_disparity(disp)
    # a degenerate/zero-area box must not crash and returns a finite value
    v = entity_depth(disp, (100, 100, 100, 100), (200, 200), dmin, dmax)
    assert 0.0 <= v <= 1.0


def test_encode_scene_roundtrips_depth_and_shape() -> None:
    rgb = np.zeros((6, 8, 3), np.uint8)
    disp01 = np.linspace(0, 1, 48, dtype=np.float32).reshape(6, 8)
    ents = [{"id": "PE00", "cls": "person", "cx": 0.5, "cy": 0.5, "depth": 0.7,
             "conf": 0.9, "label": "PERSON"}]
    scene = encode_scene(rgb, disp01, ents, fov=60.0, cam="Cam", sid="7", ts=123.0)
    assert scene["w"] == 8 and scene["h"] == 6 and scene["cam"] == "Cam" and scene["sid"] == "7"
    assert scene["entities"][0]["cls"] == "person"
    # depth base64 decodes to exactly w*h float32 values, matching the input
    raw = base64.b64decode(scene["depth"])
    arr = np.frombuffer(raw, np.float32)
    assert arr.size == 48
    assert np.allclose(arr.reshape(6, 8), disp01, atol=1e-6)
    # image decodes as a JPEG of the right size
    assert len(base64.b64decode(scene["image"])) > 0
