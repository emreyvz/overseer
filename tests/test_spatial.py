# tests/test_spatial.py
import base64

import numpy as np

from server.spatial import (
    complete_background,
    encode_scene,
    entity_depth,
    foreground_mask,
    normalize_disparity,
)


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


def test_complete_background_fills_foreground_box() -> None:
    # a near foreground blob over a far background; its box should be inpainted back to background
    disp = np.full((40, 40), 0.2, np.float32)   # background = far
    disp[10:30, 10:30] = 0.9                     # foreground object = near
    rgb = np.zeros((40, 40, 3), np.uint8)
    rgb[10:30, 10:30] = (0, 0, 255)              # red object on black bg
    boxes = [(10 / 40, 10 / 40, 30 / 40, 30 / 40)]
    bg_rgb, bg_disp = complete_background(rgb, disp, boxes)
    # inside the box, the depth is pulled back toward the surrounding background (far)
    assert bg_disp[20, 20] < 0.5
    # and the red object texture is gone (inpainted from the black surroundings)
    assert bg_rgb[20, 20][2] < 128
    # outside the box is untouched
    assert bg_disp[2, 2] == disp[2, 2]


def test_complete_background_no_boxes_is_identity() -> None:
    disp = np.full((8, 8), 0.5, np.float32)
    rgb = np.zeros((8, 8, 3), np.uint8)
    bg_rgb, bg_disp = complete_background(rgb, disp, [])
    assert np.array_equal(bg_disp, disp) and np.array_equal(bg_rgb, rgb)


def test_foreground_mask_flags_near_object_only() -> None:
    # a near, discrete object (smaller than the ~scene/10 opening kernel) on a far background:
    # only the object should be flagged; the far background stays clear.
    disp = np.full((120, 120), 0.2, np.float32)
    disp[54:66, 54:66] = 0.9        # 12px object, kernel ~13px -> removed by the opening
    m = foreground_mask(disp)
    assert m.shape == disp.shape and m.dtype == np.uint8
    assert m[60, 60] == 255         # inside the near object
    assert m[5, 5] == 0             # far background is not foreground
    # a perfectly flat scene has no foreground
    assert not foreground_mask(np.full((120, 120), 0.5, np.float32)).any()


def test_complete_background_extra_mask_fills_without_boxes() -> None:
    # no detector boxes, but the depth-derived extra_mask still drives inpainting behind the object
    disp = np.full((40, 40), 0.2, np.float32)
    disp[10:30, 10:30] = 0.9
    rgb = np.zeros((40, 40, 3), np.uint8)
    rgb[10:30, 10:30] = (0, 0, 255)
    extra = np.zeros((40, 40), np.uint8)
    extra[10:30, 10:30] = 255
    bg_rgb, bg_disp = complete_background(rgb, disp, [], extra_mask=extra)
    assert bg_disp[20, 20] < 0.5           # depth pulled back to background
    assert bg_rgb[20, 20][2] < 128         # red object inpainted away
    assert bg_disp[2, 2] == disp[2, 2]     # outside untouched


def test_encode_scene_includes_background_layer_when_supplied() -> None:
    rgb = np.zeros((6, 8, 3), np.uint8)
    disp01 = np.full((6, 8), 0.5, np.float32)
    scene = encode_scene(rgb, disp01, [], fov=60.0, cam="C", sid="1", ts=0.0,
                         bg_rgb=rgb.copy(), bg_disp01=disp01.copy())
    assert "bg_image" in scene and "bg_depth" in scene
    arr = np.frombuffer(base64.b64decode(scene["bg_depth"]), np.float32)
    assert arr.size == 48
