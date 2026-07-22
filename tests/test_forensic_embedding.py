from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from forensic.embedding import ModelAttributes, ReidEmbedder  # noqa: E402


def _script_meanpool(path: Path) -> None:
    class Tiny(torch.nn.Module):
        def forward(self, x: torch.Tensor) -> torch.Tensor:  # N,3,H,W -> N,3
            return x.mean(dim=[2, 3])
    torch.jit.script(Tiny()).save(str(path))


def _script_logits(path: Path, num: int) -> None:
    class Head(torch.nn.Module):
        def __init__(self, n: int) -> None:
            super().__init__()
            self.n = n
        def forward(self, x: torch.Tensor) -> torch.Tensor:  # N,3,H,W -> N,num
            pooled = x.mean(dim=[2, 3])                       # N,3
            reps = pooled.repeat(1, (self.n // 3) + 1)[:, : self.n]
            return reps
    torch.jit.script(Head(num)).save(str(path))


def test_reid_embed_normalized(tmp_path: Path) -> None:
    model = tmp_path / "reid.ts"
    _script_meanpool(model)
    emb = ReidEmbedder(model, "cpu")
    crops = [np.full((80, 40, 3), 128, np.uint8), np.zeros((60, 30, 3), np.uint8)]
    out = emb.embed(crops)
    assert out.shape == (2, 3)
    norms = np.linalg.norm(out, axis=1)
    assert np.allclose(norms[norms > 0], 1.0, atol=1e-4)


def test_reid_embed_empty(tmp_path: Path) -> None:
    model = tmp_path / "reid.ts"
    _script_meanpool(model)
    assert ReidEmbedder(model, "cpu").embed([]).shape == (0, 0)


def test_model_attributes_classify(tmp_path: Path) -> None:
    model = tmp_path / "par.ts"
    labels = ["tisort", "gomlek", "ceket"]
    _script_logits(model, len(labels))
    ma = ModelAttributes(model, "cpu", labels)
    result = ma.classify([np.full((80, 40, 3), 200, np.uint8)])
    assert len(result) == 1
    assert result[0] in labels
