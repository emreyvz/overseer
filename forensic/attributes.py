"""Classical (model-free) appearance attributes + accessory association."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from forensic.palette import dominant_color_name_conf
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
    """Colours from HSV over the subject's central column (to cut background bleed),
    height band and build from bbox geometry. No model. attr_conf carries how reliable the
    colour read was, so search can down-weight noisy attributes.

    Note: height_band/build are bbox-geometry proxies and are inherently camera-relative
    (a person close to the lens reads 'tall'); they are kept for coarse filtering but the
    stored attr_conf reflects the colour evidence, which is the discriminative attribute."""

    def extract(
        self, crop_bgr: np.ndarray, bbox: tuple[int, int, int, int],
        frame_hw: tuple[int, int],
    ) -> AttributeSet:
        ch, cw = crop_bgr.shape[:2]
        # central column: the subject is centred in a tight detection box; trimming the
        # left/right thirds removes most of the background that contaminated the colour.
        if cw >= 5:
            x0, x1c = int(cw * 0.2), int(cw * 0.8)
            core = crop_bgr[:, x0:x1c]
        else:
            core = crop_bgr
        if core.shape[0] >= 2:
            upper = core[: core.shape[0] // 2]
            lower = core[core.shape[0] // 2:]
        else:
            upper = lower = core
        up_name, up_conf = dominant_color_name_conf(upper, ignore_skin=True)   # bare torso isn't clothing
        lo_name, lo_conf = dominant_color_name_conf(lower, ignore_skin=True)   # bare legs above shorts either
        x1, y1, x2, y2 = bbox
        bh = max(1, y2 - y1)
        bw = max(1, x2 - x1)
        h_frame = max(1, frame_hw[0])
        # Perspective-normalized stature: divide the box-height fraction by the foot position so a
        # close (large) and a far (small) person of the same height read alike, instead of the raw
        # fraction that labelled nearly everyone "short".
        fy = min(1.0, y2 / h_frame)
        stature = (bh / h_frame) / max(0.30, fy)
        cm = int(round(max(150.0, min(200.0, 120.0 + stature * 130.0))))
        height_band = "short" if cm < 168 else ("tall" if cm > 182 else "medium")
        aspect = bw / bh
        build = "slim" if aspect < 0.35 else ("broad" if aspect > 0.5 else "medium")
        return AttributeSet(
            upper_color=up_name,
            lower_color=lo_name,
            height_band=height_band,
            build=build,
            attr_conf=round(min(up_conf, lo_conf), 4),
        )


def _mode(values: list) -> tuple[object, float]:
    """Most common value + its agreement fraction. Deterministic tie-break by str()."""
    counts: dict = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    best = max(counts.items(), key=lambda kv: (kv[1], str(kv[0])))
    return best[0], best[1] / len(values)


def vote_attributes(samples: list[AttributeSet]) -> AttributeSet:
    """Aggregate several samples of the SAME tracklet into one, using the modal value per
    field so a single noisy frame can't define the tracklet. attr_conf becomes the mean
    per-field agreement scaled by the samples' own colour confidence — high only when the
    frames agree AND their colour reads were reliable. Accessories are unioned (seeing a
    backpack in some frames is enough to record it)."""
    if not samples:
        raise ValueError("vote_attributes requires at least one sample")
    up, up_a = _mode([s.upper_color for s in samples])
    lo, lo_a = _mode([s.lower_color for s in samples])
    hb, hb_a = _mode([s.height_band for s in samples])
    bd, bd_a = _mode([s.build for s in samples])
    clothes = [s.clothing_type for s in samples if s.clothing_type]
    clothing = _mode(clothes)[0] if clothes else None
    accessories: list[str] = []
    for s in samples:
        for a in s.accessories:
            if a not in accessories:
                accessories.append(a)
    agreement = (up_a + lo_a + hb_a + bd_a) / 4.0
    mean_conf = sum(s.attr_conf for s in samples) / len(samples)
    return AttributeSet(
        upper_color=up, lower_color=lo, height_band=hb, build=bd,
        clothing_type=clothing, accessories=accessories,
        attr_conf=round(agreement * mean_conf, 4),
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
