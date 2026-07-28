"""The factory must assemble a working engine from real config even with NO model weights
present — falling back to the deterministic baseline and reporting it."""
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from core.config import load_config
from match.engine import SourceFrames
from match.factory import build_engine
from match.types import Query


@dataclass
class FakeDet:
    bbox: tuple
    category: str
    confidence: float = 0.9


def _detect_full(frame):
    h, w = frame.shape[:2]
    return [FakeDet((0, 0, w, h), "person")]


def _pattern(h=80, w=40):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[: h // 2] = (0, 0, 200)
    img[h // 2:] = (200, 0, 0)
    return img


def test_factory_falls_back_to_baseline_without_weights(tmp_path: Path) -> None:
    cfg = load_config(Path("config/default.yaml"))
    # models_dir is empty -> every specialized encoder path is missing -> baseline
    engine, info = build_engine(cfg, _detect_full, models_dir=tmp_path,
                                category_to_cls={"person": "person"})
    assert info["person"] == "baseline-grid-v1"
    assert info["vehicle"] == "baseline-grid-v1"
    assert info["segmenter"] == "ellipse"


def test_factory_engine_runs_end_to_end(tmp_path: Path) -> None:
    cfg = load_config(Path("config/default.yaml"))
    engine, _info = build_engine(cfg, _detect_full, models_dir=tmp_path,
                                 category_to_cls={"person": "person"})
    q = Query(cls="person", crop=_pattern())
    sources = [SourceFrames(1, "cam-A", [_pattern()] * 3)]
    res = engine.match(q, sources)
    assert len(res.hits) == 1
    assert res.hits[0].source_id == 1
    assert res.hits[0].evidence.model_id == "baseline-grid-v1"
