"""Face detection for portrait focus. Wraps OpenCV's YuNet (FaceDetectorYN), auto-downloading the
~0.3 MB ONNX model. `crop_face(bgr)` returns a portrait crop centred on the subject's face (with
hair headroom + shoulders), or None when no face is found or the model is unavailable.

Used by the roster profile so the hero photo focuses on the face instead of the body.
"""
from __future__ import annotations

import threading
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger as log

_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
        "face_detection_yunet_2023mar.onnx")
_FILE = "face_detection_yunet.onnx"


class FaceDetector:
    """Lazy YuNet wrapper. Never fatal: any failure disables the feature (crop returns None)."""

    def __init__(self, models_dir: str | Path = "models") -> None:
        self._dir = Path(models_dir)
        self._det: Any = None
        self._failed = False
        self._lock = threading.Lock()

    def _weights(self) -> Path | None:
        p = self._dir / _FILE
        if p.exists() and p.stat().st_size > 50_000:
            return p
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            log.info("downloading face detector (~0.3 MB, one time)...")
            urllib.request.urlretrieve(_URL, p)
            return p if p.exists() and p.stat().st_size > 50_000 else None
        except Exception as exc:  # noqa: BLE001
            log.warning("face detector weights unavailable: %s", exc)
            return None

    def _ensure(self) -> bool:
        if self._det is not None:
            return True
        if self._failed:
            return False
        with self._lock:
            if self._det is not None:
                return True
            if self._failed:
                return False
            try:
                import cv2
                w = self._weights()
                if w is None:
                    self._failed = True
                    return False
                # score/nms thresholds tuned for stored crops (favour recall on one clear face)
                self._det = cv2.FaceDetectorYN.create(str(w), "", (320, 320), 0.6, 0.3, 5000)
                return True
            except Exception:  # noqa: BLE001
                log.exception("face detector load failed - portrait face-focus disabled")
                self._failed = True
                return False

    def detect(self, bgr: np.ndarray) -> tuple[float, float, float, float] | None:
        """Highest-confidence face box (x, y, w, h) in pixels, or None."""
        if bgr is None or getattr(bgr, "size", 0) == 0 or not self._ensure():
            return None
        try:
            import cv2
            h, w = bgr.shape[:2]
            self._det.setInputSize((int(w), int(h)))
            _, faces = self._det.detect(bgr)
            if faces is None or len(faces) == 0:
                return None
            # faces: Nx15 -> x, y, w, h, 5 landmarks (x,y), score. Prefer the biggest, most confident.
            best = max(faces, key=lambda f: float(f[2]) * float(f[3]) * float(f[-1]))
            return (float(best[0]), float(best[1]), float(best[2]), float(best[3]))
        except Exception:  # noqa: BLE001
            return None

    def crop_face(self, bgr: np.ndarray, aspect: float = 0.8) -> np.ndarray | None:
        """A portrait crop (w/h = aspect, default 4:5) centred on the face, with hair headroom
        above and shoulders below. Returns None if no face is found."""
        box = self.detect(bgr)
        if box is None:
            return None
        H, W = bgr.shape[:2]
        x, y, fw, fh = box
        ccx = x + fw / 2.0
        cface = y + fh / 2.0
        cw = fw * 2.4                       # room for both sides of the head
        ch = cw / aspect                    # taller portrait frame
        # place the face at ~40% from the top: hair above, chest/shoulders below
        y0 = cface - ch * 0.40
        y1 = cface + ch * 0.60
        x0 = ccx - cw / 2.0
        x1 = ccx + cw / 2.0
        x0i, y0i = int(max(0, x0)), int(max(0, y0))
        x1i, y1i = int(min(W, x1)), int(min(H, y1))
        if x1i - x0i < 8 or y1i - y0i < 8:
            return None
        return bgr[y0i:y1i, x0i:x1i]


_shared: FaceDetector | None = None


def crop_face(bgr: np.ndarray, models_dir: str | Path = "models") -> np.ndarray | None:
    """Module-level convenience using a shared lazy detector."""
    global _shared
    if _shared is None:
        _shared = FaceDetector(models_dir)
    return _shared.crop_face(bgr)
