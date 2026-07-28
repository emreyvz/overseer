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


def dominant_color_name_conf(crop_bgr: np.ndarray) -> tuple[str, float]:
    """Named dominant colour plus a confidence in [0,1]. Confidence reflects how
    concentrated the evidence is: for a neutral result, the fraction of achromatic pixels;
    for a coloured result, how tightly the chromatic pixels cluster around the winning hue.
    A crop of mixed colours (e.g. background bleeding in) yields low confidence, which the
    caller stores as attr_conf so search can down-weight or filter unreliable attributes."""
    if crop_bgr.size == 0:
        return ("unknown", 0.0)
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    h = hsv[..., 0].reshape(-1)
    s = hsv[..., 1].reshape(-1)
    v = hsv[..., 2].reshape(-1)
    achromatic = s < 40
    ach_frac = float(achromatic.mean())
    if ach_frac > 0.6:
        mv = float(v[achromatic].mean()) if achromatic.any() else float(v.mean())
        name = "black" if mv < 60 else ("gray" if mv < 170 else "white")
        return (name, ach_frac)
    chroma = ~achromatic
    n_chroma = int(chroma.sum())
    if n_chroma == 0:
        return ("unknown", 0.0)
    hist = np.bincount(h[chroma], minlength=180)
    dom = int(hist.argmax())
    # concentration: share of chromatic pixels within ~±16 degrees (±8 bins) of the peak
    idx = (np.arange(dom - 8, dom + 9) % 180)
    concentration = float(hist[idx].sum()) / n_chroma
    return (_hue_to_name(dom * 2.0, float(v[chroma].mean())), concentration)


def dominant_color_name(crop_bgr: np.ndarray) -> str:
    return dominant_color_name_conf(crop_bgr)[0]
