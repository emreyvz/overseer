"""Active-tracklet lifecycle: representative sampling, accessory tagging, expiry."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

import numpy as np

from camera.frame_buffer import Frame
from core.config import Config
from forensic.attributes import AttributeSet, ClassicalAttributes, associate_accessories
from plugins.base import Detection

EnsureFn = Callable[[int | None, int | None, float, float], int]


@dataclass
class CropJob:
    tracklet_id: int
    crop: np.ndarray
    ts: float
    attributes: AttributeSet


@dataclass
class TrackletView:
    tracklet_id: int
    track_id: int | None
    bbox: tuple[int, int, int, int]
    attributes: AttributeSet


@dataclass
class _Active:
    db_id: int
    last_ts: float
    last_sample_ts: float
    best_area: float
    last_attrs: AttributeSet | None = None


def _area(bbox: tuple[int, int, int, int]) -> float:
    return float(max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1]))


def _clamp_bbox(image_shape: tuple[int, ...], bbox: tuple[int, int, int, int]
                 ) -> tuple[int, int, int, int]:
    """Clamp bbox to frame boundaries."""
    h, w = image_shape[:2]
    x1 = max(0, min(bbox[0], w - 1))
    y1 = max(0, min(bbox[1], h - 1))
    x2 = max(x1 + 1, min(bbox[2], w))
    y2 = max(y1 + 1, min(bbox[3], h))
    return (x1, y1, x2, y2)


def _crop_copy(image: np.ndarray, bbox: tuple[int, int, int, int]) -> np.ndarray:
    """Extract contiguous crop from PRE-CLAMPED bbox."""
    x1, y1, x2, y2 = bbox
    return np.ascontiguousarray(image[y1:y2, x1:x2].copy())


class TrackletManager:
    def __init__(self, config: Config, ensure_tracklet: EnsureFn,
                 extractor: ClassicalAttributes) -> None:
        self._ensure = ensure_tracklet
        self._extractor = extractor
        self._sample_interval = float(config.get("forensic.sample_interval_seconds", 2.0))
        self._expire = float(config.get("forensic.expire_seconds", 5.0))
        self._accessory_iou = float(config.get("forensic.accessory_iou", 0.1))
        self._active: dict[tuple[int | None, int | None], _Active] = {}

    def reset(self) -> None:
        self._active.clear()

    def update(
        self, source_id: int | None, frame: Frame, persons: list[Detection],
        accessories: list[Detection], now: float,
    ) -> tuple[list[CropJob], list[TrackletView]]:
        jobs: list[CropJob] = []
        views: list[TrackletView] = []
        for det in persons:
            if det.track_id is None:
                continue  # forensic only tracks identified persons
            key = (source_id, det.track_id)
            active = self._active.get(key)
            if active is None or now - active.last_ts >= self._expire:
                db_id = self._ensure(source_id, det.track_id, now, now)
                active = _Active(db_id=db_id, last_ts=now, last_sample_ts=-1e18,
                                 best_area=0.0)
                self._active[key] = active
            active.last_ts = now
            # Clamp bbox once and use it everywhere for consistency
            bbox = _clamp_bbox(frame.image.shape, det.bbox)
            area = _area(bbox)
            due = (now - active.last_sample_ts >= self._sample_interval
                   or area > active.best_area * 1.5)
            acc_names = associate_accessories(bbox, accessories, self._accessory_iou)
            if due:
                crop = _crop_copy(frame.image, bbox)
                attrs = self._extractor.extract(crop, bbox, frame.image.shape[:2])
                attrs.accessories = list(acc_names)
                active.last_sample_ts = now
                active.best_area = max(active.best_area, area)
                active.last_attrs = attrs
                jobs.append(CropJob(active.db_id, crop, now, attrs))
            view_attrs = active.last_attrs or self._extractor.extract(
                _crop_copy(frame.image, bbox), bbox, frame.image.shape[:2])
            views.append(TrackletView(active.db_id, det.track_id, bbox,
                                      replace(view_attrs, accessories=list(acc_names))))
        for key in [k for k, a in self._active.items() if now - a.last_ts >= self._expire]:
            del self._active[key]
        return jobs, views
