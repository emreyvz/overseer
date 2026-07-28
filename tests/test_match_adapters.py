"""Adapters for real models must degrade gracefully: when weights/libraries are absent,
available() is False and nothing crashes. Real inference accuracy is measured separately by
the eval harness when weights are present."""
from __future__ import annotations

import numpy as np

from match.anpr.reader import OcrPlateReader
from match.encoders.baseline import DeterministicEncoder
from match.encoders.resolve import best_available
from match.encoders.torchscript import TorchScriptEncoder
from match.seg_backend import YoloSegBackend
from match.segmentation import Segmenter


def test_torchscript_encoder_missing_weights_is_unavailable() -> None:
    enc = TorchScriptEncoder("does/not/exist.torchscript", "reid-person-osnet")
    assert enc.available() is False
    # encode must not raise when unavailable
    out = enc.encode([np.zeros((16, 16, 3), dtype=np.uint8)])
    assert out.shape[0] == 0


def test_torchscript_load_is_cached() -> None:
    enc = TorchScriptEncoder("does/not/exist.torchscript", "reid")
    assert enc.available() is False
    assert enc.available() is False   # second call uses the cached negative result


def test_best_available_prefers_available() -> None:
    baseline = DeterministicEncoder()
    missing = TorchScriptEncoder("nope.ts", "reid")
    enc, used_fallback = best_available([missing], baseline)
    assert enc is baseline
    assert used_fallback is True


def test_best_available_picks_working_candidate() -> None:
    baseline = DeterministicEncoder()
    working = DeterministicEncoder()   # available() True stands in for a loaded model
    enc, used_fallback = best_available([working], baseline)
    assert enc is working
    assert used_fallback is False


def test_ocr_reader_unavailable_returns_no_reads() -> None:
    reader = OcrPlateReader()
    if not reader.available():         # environment has no OCR lib -> exercise the fallback
        assert reader(np.zeros((32, 64, 3), dtype=np.uint8)) == []


def test_yolo_seg_missing_weights_unavailable_and_segmenter_falls_back() -> None:
    backend = YoloSegBackend("does/not/exist-seg.pt")
    assert backend.available() is False
    assert backend.mask(np.zeros((10, 10, 3), dtype=np.uint8), "person") is None
    # Segmenter with an unavailable backend still returns the deterministic ellipse
    seg = Segmenter(backend=backend)
    m, cov = seg.mask(np.zeros((40, 20, 3), dtype=np.uint8), "person")
    assert m.shape == (40, 20)
    assert cov > 0.0
