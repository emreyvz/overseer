"""Named dominant-color naming from a BGR crop (English color names)."""
from __future__ import annotations

import cv2
import numpy as np


def _hue_to_name(deg: float, value: float) -> str:
    if deg < 15 or deg >= 345:
        base = "red"
    elif deg < 40:
        base = "orange"
    elif deg < 70:
        base = "yellow"
    elif deg < 170:
        base = "green"
    elif deg < 200:
        base = "cyan"
    elif deg < 260:
        base = "blue"
    elif deg < 320:
        base = "purple"
    else:
        base = "pink"
    if base == "orange" and value < 120:
        return "brown"
    return base


def dominant_color_name(crop_bgr: np.ndarray) -> str:
    if crop_bgr.size == 0:
        return "unknown"
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    h = hsv[..., 0].reshape(-1)
    s = hsv[..., 1].reshape(-1)
    v = hsv[..., 2].reshape(-1)
    achromatic = s < 40
    if achromatic.mean() > 0.6:
        mv = float(v[achromatic].mean()) if achromatic.any() else float(v.mean())
        if mv < 60:
            return "black"
        if mv < 170:
            return "gray"
        return "white"
    chroma = ~achromatic
    hist = np.bincount(h[chroma], minlength=180)
    dom = int(hist.argmax())
    return _hue_to_name(dom * 2.0, float(v[chroma].mean()))
