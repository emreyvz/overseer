import numpy as np

from match.encoders.baseline import DeterministicEncoder
from match.encoders.base import l2_normalize
from match.scoring import cosine
from match.segmentation import Segmenter


def _solid(color, h=64, w=32):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:, :] = color
    return img


def test_encode_returns_normalized_rows() -> None:
    enc = DeterministicEncoder()
    out = enc.encode([_solid((10, 20, 200)), _solid((200, 10, 10))])
    assert out.shape == (2, enc.dim)
    for row in out:
        assert abs(np.linalg.norm(row) - 1.0) < 1e-5 or np.linalg.norm(row) == 0.0


def test_encode_empty() -> None:
    enc = DeterministicEncoder()
    out = enc.encode([])
    assert out.shape == (0, enc.dim)


def test_identical_crops_cosine_one() -> None:
    enc = DeterministicEncoder()
    a = _solid((30, 60, 200))
    v = enc.encode([a, a.copy()])
    assert abs(cosine(v[0], v[1]) - 1.0) < 1e-6


def test_deterministic_across_calls() -> None:
    enc = DeterministicEncoder()
    crop = _solid((120, 30, 90))
    assert np.array_equal(enc.encode([crop]), enc.encode([crop]))


def test_patterned_crops_are_discriminative() -> None:
    enc = DeterministicEncoder()
    # a red-top/black-bottom person vs an all-red blob: must NOT look identical
    split = np.zeros((64, 32, 3), dtype=np.uint8)
    split[:32] = (0, 0, 200)      # top red
    split[32:] = (0, 0, 0)        # bottom black
    allred = _solid((0, 0, 200))
    v = enc.encode([split, allred])
    assert cosine(v[0], v[1]) < 0.99


def test_available_and_trust() -> None:
    enc = DeterministicEncoder()
    assert enc.available() is True
    assert 0.0 < enc.trust <= 1.0
    assert enc.model_id


def test_mask_changes_embedding() -> None:
    enc = DeterministicEncoder()
    crop = _solid((0, 0, 200), h=64, w=64)
    seg = Segmenter()
    m, _ = seg.mask(crop, "person")
    plain = enc.encode([crop])[0]
    masked = enc.encode([crop], [m])[0]
    # zeroing the corners changes the signature
    assert cosine(plain, masked) < 1.0


def test_l2_normalize_zero_row_stays_zero() -> None:
    out = l2_normalize(np.zeros((1, 4), dtype=np.float32))
    assert np.array_equal(out, np.zeros((1, 4), dtype=np.float32))


def test_segmenter_fallback_ellipse() -> None:
    seg = Segmenter()
    crop = _solid((10, 10, 10), h=80, w=40)
    m, cov = seg.mask(crop, "person")
    assert m.shape == (80, 40)
    assert 0.3 < cov < 0.9
    assert m[40, 20]           # center is foreground
    assert not m[0, 0]         # corner is background


def test_segmenter_deterministic() -> None:
    seg = Segmenter()
    crop = _solid((10, 10, 10), h=50, w=50)
    m1, _ = seg.mask(crop)
    m2, _ = seg.mask(crop)
    assert np.array_equal(m1, m2)


def test_segmenter_empty_crop() -> None:
    seg = Segmenter()
    m, cov = seg.mask(np.empty((0, 0, 3), dtype=np.uint8))
    assert cov == 0.0
