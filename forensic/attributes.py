"""Classical (model-free) appearance attributes + accessory association."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from forensic.palette import dominant_color_name
from plugins.base import Detection


@dataclass
class AttributeSet:
    upper_color: str
    lower_color: str
    height_band: str
    build: str
    clothing_type: str | None = None
    accessories: list[str] = field(default_factory=list)
    attr_conf: float = 1.0


class ClassicalAttributes:
    """Colors from HSV, height band and build from bbox geometry. No model."""

    def extract(
        self, crop_bgr: np.ndarray, bbox: tuple[int, int, int, int],
        frame_hw: tuple[int, int],
    ) -> AttributeSet:
        ch = crop_bgr.shape[0]
        if ch >= 2:
            upper = crop_bgr[: ch // 2]
            lower = crop_bgr[ch // 2:]
        else:
            upper = lower = crop_bgr
        x1, y1, x2, y2 = bbox
        bh = max(1, y2 - y1)
        bw = max(1, x2 - x1)
        h_frame = max(1, frame_hw[0])
        ratio = bh / h_frame
        height_band = "short" if ratio < 0.33 else ("medium" if ratio < 0.66 else "tall")
        aspect = bw / bh
        build = "slim" if aspect < 0.35 else ("broad" if aspect > 0.5 else "medium")
        return AttributeSet(
            upper_color=dominant_color_name(upper),
            lower_color=dominant_color_name(lower),
            height_band=height_band,
            build=build,
        )


def _containment(person: tuple[int, int, int, int],
                 acc: tuple[int, int, int, int]) -> float:
    px1, py1, px2, py2 = person
    ax1, ay1, ax2, ay2 = acc
    ix1, iy1 = max(px1, ax1), max(py1, ay1)
    ix2, iy2 = min(px2, ax2), min(py2, ay2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter == 0:
        return 0.0
    area_acc = max(1, (ax2 - ax1) * (ay2 - ay1))
    return inter / area_acc


def associate_accessories(
    person_bbox: tuple[int, int, int, int], accessories: list[Detection],
    iou_thresh: float,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for acc in accessories:
        if _containment(person_bbox, acc.bbox) >= iou_thresh and acc.label not in seen:
            seen.add(acc.label)
            out.append(acc.label)
    return out
