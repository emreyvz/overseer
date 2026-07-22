"""Keypoint pose behaviours (feature 1): hand-raise via YOLO-pose keypoints.
Best-effort, lazy-loaded, low frame rate. Fall/fight/crowd already come from the
bbox-heuristic pose monitor in the main pipeline."""
from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger("overseer.pose")

# COCO-17 keypoint indices
_NOSE, _L_SH, _R_SH, _L_WR, _R_WR = 0, 5, 6, 9, 10
_KP_CONF = 0.55  # keypoint confidence floor — reject phantom skeletons


class PoseKP:
    def __init__(self) -> None:
        self._model = None
        self._failed = False

    def _ensure(self) -> bool:
        if self._model is not None:
            return True
        if self._failed:
            return False
        try:
            from ultralytics import YOLO
            self._model = YOLO("yolo11n-pose.pt")  # auto-downloads on first use
            log.info("pose-keypoint model ready")
            return True
        except Exception as exc:  # noqa: BLE001
            self._failed = True
            log.warning("pose-keypoint unavailable: %s", exc)
            return False

    def detect(self, frame: np.ndarray) -> list[dict]:
        """Return [{behavior, bbox(normalized)}] for hand-raise poses."""
        if not self._ensure():
            return []
        h, w = frame.shape[:2]
        try:
            res = self._model(frame, verbose=False, conf=0.4)[0]  # type: ignore[index]
        except Exception:  # noqa: BLE001
            return []
        kps, boxes = res.keypoints, res.boxes
        if kps is None or kps.xy is None or boxes is None or kps.conf is None:
            return []
        out: list[dict] = []
        xy = kps.xy.cpu().numpy() if hasattr(kps.xy, "cpu") else np.asarray(kps.xy)
        cf = kps.conf.cpu().numpy() if hasattr(kps.conf, "cpu") else np.asarray(kps.conf)
        bx = boxes.xyxy.cpu().numpy() if hasattr(boxes.xyxy, "cpu") else np.asarray(boxes.xyxy)
        for i in range(len(xy)):
            k, c = xy[i], cf[i]
            def ok(idx: int) -> bool:
                return c[idx] >= _KP_CONF and (k[idx][0] > 0 or k[idx][1] > 0)
            # A genuine hand-raise: a confidently-tracked wrist is ABOVE the nose
            # (a resting/gesturing arm never clears the head). Much stricter than
            # "wrist above shoulder", which fired on ordinary poses.
            raise_l = ok(_L_WR) and ok(_NOSE) and k[_L_WR][1] < k[_NOSE][1]
            raise_r = ok(_R_WR) and ok(_NOSE) and k[_R_WR][1] < k[_NOSE][1]
            if raise_l or raise_r:
                b = bx[i]
                out.append({"behavior": "HAND RAISE",
                            "bbox": [float(b[0]) / w, float(b[1]) / h,
                                     (float(b[2]) - float(b[0])) / w, (float(b[3]) - float(b[1])) / h]})
        return out
