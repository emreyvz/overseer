"""Appearance-signature matching (catalog 14/15/19). The part-based (torso/legs)
signature plus gray-world white balance must tell a red-top/black-jeans person apart
from an all-red one — the failure the operator hit when a parked-car search locked
onto the sky. Tests the pure helpers, no camera/DB needed."""
import numpy as np

from server.backend import Backend


def _person(top_bgr, bottom_bgr, h=120, w=48):
    """Synthetic person crop: solid upper band over a solid lower band."""
    img = np.zeros((h, w, 3), np.uint8)
    cut = int(h * 0.55)
    img[:cut] = top_bgr
    img[cut:] = bottom_bgr
    return img


def _split_sig(img):
    """Build a split signature the way Backend._appearance_sig(split=True) does."""
    img = Backend._gray_world(img)
    cut = int(img.shape[0] * 0.55)
    return ("split", Backend._hs_hist(img[:cut]), Backend._hs_hist(img[cut:]))


def test_part_signature_separates_similar_colours():
    red, black, blue = (0, 0, 200), (20, 20, 20), (200, 0, 0)
    query = _split_sig(_person(red, black))          # red top, black legs
    same = _split_sig(_person(red, black))           # identical outfit
    all_red = _split_sig(_person(red, red))          # all red — a whole-blob match would confuse these
    other = _split_sig(_person(blue, black))         # blue top, black legs

    s_same = Backend._compare_sig(query, same)
    s_allred = Backend._compare_sig(query, all_red)
    s_other = Backend._compare_sig(query, other)

    assert s_same > 0.9                    # identical outfit is a strong match
    assert s_same > s_allred               # the all-red decoy scores lower than the real match
    assert s_same > s_other                # a different top scores lower too


def test_gray_world_neutralises_colour_cast():
    """The same crop seen through a camera with a warmer/cooler channel gain (a
    multiplicative cast — what gray-world is built to correct) should still match its
    neutral original strongly after white balancing (colour constancy, catalog 14)."""
    base = _person((40, 40, 200), (40, 40, 40))       # red-ish top, dark legs (non-zero so gain is visible)
    cast = base.astype(np.float32)
    cast[..., 0] = np.clip(cast[..., 0] * 1.4, 0, 255)  # 40% cooler blue gain
    cast[..., 2] = np.clip(cast[..., 2] * 0.85, 0, 255)  # slightly weaker red gain
    cast = cast.astype(np.uint8)

    balanced = Backend._compare_sig(_split_sig(base), _split_sig(cast))
    assert balanced > 0.5                  # survives a channel-gain cast that would otherwise break the match


def test_whole_signature_still_compares():
    a = Backend._gray_world(np.full((40, 40, 3), (0, 0, 200), np.uint8))
    b = Backend._gray_world(np.full((40, 40, 3), (0, 0, 200), np.uint8))
    sa = ("whole", Backend._hs_hist(a))
    sb = ("whole", Backend._hs_hist(b))
    assert Backend._compare_sig(sa, sb) > 0.9
