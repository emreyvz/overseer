"""Keypoint pose behaviours (feature 1): hand-raise via YOLO-pose keypoints.
Best-effort, lazy-loaded, low frame rate. Fall/fight/crowd already come from the
bbox-heuristic pose monitor in the main pipeline."""
from __future__ import annotations

import logging
import math

import numpy as np

log = logging.getLogger("overseer.pose")

# COCO-17 keypoint indices
_NOSE, _L_EYE, _R_EYE, _L_SH, _R_SH, _L_WR, _R_WR = 0, 1, 2, 5, 6, 9, 10
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

    def detect_pose(self, frame: np.ndarray) -> list[dict]:
        """Raw per-person skeletons for gait/soft-biometrics (feature 5):
        [{bbox:(x1,y1,x2,y2) px, kpts:(17,2), conf:(17,)}]. One inference; caller derives behaviours."""
        if not self._ensure():
            return []
        try:
            res = self._model(frame, verbose=False, conf=0.4)[0]  # type: ignore[index]
        except Exception:  # noqa: BLE001
            return []
        kps, boxes = res.keypoints, res.boxes
        if kps is None or kps.xy is None or boxes is None or kps.conf is None:
            return []
        xy = kps.xy.cpu().numpy() if hasattr(kps.xy, "cpu") else np.asarray(kps.xy)
        cf = kps.conf.cpu().numpy() if hasattr(kps.conf, "cpu") else np.asarray(kps.conf)
        bx = boxes.xyxy.cpu().numpy() if hasattr(boxes.xyxy, "cpu") else np.asarray(boxes.xyxy)
        out: list[dict] = []
        for i in range(len(xy)):
            b = bx[i]
            out.append({"bbox": (float(b[0]), float(b[1]), float(b[2]), float(b[3])),
                        "kpts": xy[i].astype(np.float32), "conf": cf[i].astype(np.float32)})
        return out

    @staticmethod
    def hand_raise(pose: dict, w: int, h: int) -> dict | None:
        """Derive a HAND RAISE behaviour from one raw skeleton (so a single pose inference serves both
        the behaviour and gait paths). Returns the normalized-bbox event dict, or None."""
        k, c = pose["kpts"], pose["conf"]
        def ok(idx: int) -> bool:
            return c[idx] >= _KP_CONF and (k[idx][0] > 0 or k[idx][1] > 0)
        raise_l = ok(_L_WR) and ok(_NOSE) and k[_L_WR][1] < k[_NOSE][1]
        raise_r = ok(_R_WR) and ok(_NOSE) and k[_R_WR][1] < k[_NOSE][1]
        if not (raise_l or raise_r):
            return None
        x1, y1, x2, y2 = pose["bbox"]
        return {"behavior": "HAND RAISE",
                "bbox": [x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h]}

    @staticmethod
    def facing(pose: dict) -> float | None:
        """Estimate which way a person is oriented, as a heading in IMAGE space (degrees, 0 = +x to
        the right, growing clockwise because image y points down). Derived from the shoulder line
        (facing is perpendicular to it), the nose lean along that perpendicular (left/right head turn)
        and whether the eyes are visible (facing toward the camera vs away). Coarse and best-effort:
        returns None when the shoulders are not confidently seen. Feeds the Social X-ray overlay, and
        works for a standing (non-moving) person where motion heading gives nothing."""
        k, c = pose["kpts"], pose["conf"]

        def ok(idx: int, thr: float = _KP_CONF) -> bool:
            return c[idx] >= thr and (k[idx][0] > 0 or k[idx][1] > 0)

        if not (ok(_L_SH) and ok(_R_SH)):
            return None
        lx, rx = float(k[_L_SH][0]), float(k[_R_SH][0])
        mx = (lx + rx) / 2.0                             # shoulder midpoint x
        sh_w = abs(lx - rx)                              # shoulder width in image x
        frontal = ok(_L_EYE, 0.4) or ok(_R_EYE, 0.4)    # eyes visible => facing toward the camera
        # Horizontal turn: where the nose sits within the shoulder span. Centre -> straight toward/away;
        # off to one side -> turned that way (a profile view has narrow shoulders, so the offset saturates).
        hx = 0.0
        if ok(_NOSE) and sh_w > 1e-3:
            hx = max(-1.0, min(1.0, (float(k[_NOSE][0]) - mx) / (0.5 * sh_w)))
        # Depth component: toward the viewer (down the image, +y) when the face is visible, away (up) if not.
        # Its magnitude shrinks as the horizontal turn grows, so a strong side turn reads as sideways.
        vy = (1.0 if frontal else -1.0) * math.sqrt(max(0.0, 1.0 - hx * hx))
        if abs(hx) < 1e-3 and abs(vy) < 1e-3:
            return None
        return round(math.degrees(math.atan2(vy, hx)), 1)   # image-space heading (0 = right, +90 = down/toward)

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
