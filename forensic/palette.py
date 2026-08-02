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


def _name_bgr(bgr: np.ndarray) -> str:
    """Name a single BGR colour: achromatic (black/gray/white) by value when weakly saturated,
    else by hue."""
    px = np.uint8([[[max(0, min(255, int(bgr[0]))), max(0, min(255, int(bgr[1]))), max(0, min(255, int(bgr[2])))]]])
    hsv = cv2.cvtColor(px, cv2.COLOR_BGR2HSV)[0][0]
    hh, s, v = float(hsv[0]), float(hsv[1]), float(hsv[2])
    if v < 68:                 # very dark = black, even with a faint hue (a black car reflects blue-ish)
        return "black"
    if s < 60:                 # weakly saturated = neutral
        return "gray" if v < 180 else "white"
    return _hue_to_name(hh * 2.0, v)


def dominant_color_name_conf(crop_bgr: np.ndarray, ignore_skin: bool = False) -> tuple[str, float]:
    """Named dominant colour plus a confidence in [0,1]. Confidence reflects how
    concentrated the evidence is: for a neutral result, the fraction of achromatic pixels;
    for a coloured result, how tightly the chromatic pixels cluster around the winning hue.
    A crop of mixed colours (e.g. background bleeding in) yields low confidence, which the
    caller stores as attr_conf so search can down-weight or filter unreliable attributes.

    ``ignore_skin`` (people only): bare skin — a shirtless torso or bare legs above shorts — is
    NOT clothing, so naming it "brown/orange" would be wrong. When set, skin-toned pixels are
    dropped before naming, and a crop that is mostly bare skin returns ("unknown", 0.0) rather
    than inventing a garment colour. Left off for vehicles so a tan/beige car is unaffected."""
    if crop_bgr is None or getattr(crop_bgr, "size", 0) == 0:
        return ("unknown", 0.0)
    img = crop_bgr
    h0, w0 = img.shape[:2]
    # Central core: trim the edges, where the background around the object bleeds in. That bleed was
    # the main reason colours came out wrong (a car's road/sky, a person's surroundings).
    if h0 >= 12 and w0 >= 12:
        core = img[int(h0 * 0.12):int(h0 * 0.88), int(w0 * 0.16):int(w0 * 0.84)]
        if core.size:
            img = core
    # Downsample (denoise + speed), then cluster the pixels and name the DOMINANT cluster. Clustering
    # is robust to a two-tone object (windows/shadows on a car, a logo on a shirt) where a raw hue
    # histogram would smear; the cluster's share of pixels is the confidence.
    small = cv2.resize(img, (36, 36), interpolation=cv2.INTER_AREA)
    px = small.reshape(-1, 3).astype(np.float32)
    if ignore_skin:
        ycc = cv2.cvtColor(small, cv2.COLOR_BGR2YCrCb).reshape(-1, 3)
        cr, cb = ycc[:, 1], ycc[:, 2]
        keep = ~((cr >= 133) & (cr <= 173) & (cb >= 77) & (cb <= 127))
        if float(keep.mean()) < 0.28:        # mostly bare skin -> no reliable clothing colour
            return ("unknown", 0.0)
        px = px[keep]
    if len(px) < 8:
        return ("unknown", 0.0)
    k = int(min(3, len(px)))
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 12, 1.0)
    try:
        _compact, labels, centers = cv2.kmeans(px, k, None, crit, 2, cv2.KMEANS_PP_CENTERS)
    except Exception:  # noqa: BLE001
        centers = np.array([px.mean(axis=0)]); labels = np.zeros(len(px), np.int32)
    labels = labels.ravel()
    counts = np.bincount(labels, minlength=len(centers))
    dom = int(np.argmax(counts))
    frac = float(counts[dom]) / float(len(px))
    return (_name_bgr(centers[dom]), round(frac, 3))


def dominant_color_name(crop_bgr: np.ndarray, ignore_skin: bool = False) -> str:
    return dominant_color_name_conf(crop_bgr, ignore_skin=ignore_skin)[0]


def skin_fraction(crop_bgr: np.ndarray) -> float:
    """Share of a crop that is bare skin (YCrCb, robust across skin tones). Used to tag a shirtless
    torso as bare rather than inventing a garment colour for it."""
    if crop_bgr is None or getattr(crop_bgr, "size", 0) == 0:
        return 0.0
    ycc = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2YCrCb).reshape(-1, 3)
    cr, cb = ycc[:, 1], ycc[:, 2]
    skin = (cr >= 133) & (cr <= 173) & (cb >= 77) & (cb <= 127)
    return float(skin.mean())
