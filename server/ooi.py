"""Object-of-Interest visual tracker: register an arbitrary box and follow it
across frames by colour histogram (CamShift — works on stock OpenCV, no contrib)."""
from __future__ import annotations

import threading

import cv2
import numpy as np

_TERM = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 1)


class _OOI:
    def __init__(self, name: str, window: tuple[int, int, int, int], hist: np.ndarray) -> None:
        self.name = name
        self.window = window  # (x, y, w, h) px
        self.base = (window[2], window[3])  # registered size — clamp growth against this
        self.hist = hist
        self.lost = False


class OOIManager:
    def __init__(self) -> None:
        self._items: dict[str, _OOI] = {}
        self._lock = threading.RLock()
        self._seq = 0

    def register(self, name: str, bbox_px: tuple[int, int, int, int], frame: np.ndarray) -> str | None:
        x, y, w, h = bbox_px
        if w < 4 or h < 4:
            return None
        roi = frame[max(0, y):y + h, max(0, x):x + w]
        if roi.size == 0:
            return None
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 30, 20), (180, 255, 255))
        hist = cv2.calcHist([hsv], [0], mask, [16], [0, 180])
        cv2.normalize(hist, hist, 0, 255, cv2.NORM_MINMAX)
        with self._lock:
            oid = f"OOI_{self._seq}"
            self._seq += 1
            self._items[oid] = _OOI(name, (x, y, w, h), hist)
        return oid

    def update(self, frame: np.ndarray) -> list[dict]:
        out: list[dict] = []
        with self._lock:
            if not self._items:
                return out
            fh, fw = frame.shape[:2]
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            for oid, o in self._items.items():
                dst = cv2.calcBackProject([hsv], [0], o.hist, [0, 180], 1)
                try:
                    _ret, o.window = cv2.CamShift(dst, o.window, _TERM)
                except Exception:  # noqa: BLE001
                    pass
                x, y, w, h = (int(v) for v in o.window)
                # Clamp size to the registered scale so CamShift can't balloon the box
                # to cover half the frame when similar colours appear elsewhere.
                bw, bh = o.base
                w = max(int(bw * 0.6), min(int(bw * 1.7), w or bw))
                h = max(int(bh * 0.6), min(int(bh * 1.7), h or bh))
                x = max(0, min(fw - w, x))
                y = max(0, min(fh - h, y))
                o.window = (x, y, w, h)
                conf = 0.0
                if w > 0 and h > 0:
                    region = dst[max(0, y):y + h, max(0, x):x + w]
                    conf = float(region.mean()) / 255.0 if region.size else 0.0
                o.lost = conf < 0.05 or w < 4 or h < 4
                out.append({"id": oid, "name": o.name,
                            "bbox": [x / fw, y / fh, w / fw, h / fh],
                            "lost": o.lost, "conf": round(conf, 3)})
        return out

    def remove(self, oid: str) -> None:
        with self._lock:
            self._items.pop(oid, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
