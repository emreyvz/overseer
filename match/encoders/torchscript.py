"""Real embedding encoder backed by a TorchScript module (person ReID, vehicle ReID, or a
generic image embedder like DINOv2/CLIP exported to TorchScript).

Everything heavy is imported lazily and guarded: a missing weight file or a torch import
failure makes ``available()`` return False so the engine falls back to the baseline encoder
instead of crashing. Determinism holds because inference runs in eval() with no_grad and a
fixed preprocessing pipeline."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from match.encoders.base import Encoder, apply_mask, l2_normalize

_IMAGENET_MEAN = (0.485, 0.456, 0.406)
_IMAGENET_STD = (0.229, 0.224, 0.225)


class TorchScriptEncoder(Encoder):
    def __init__(
        self,
        model_path: str | Path,
        model_id: str,
        *,
        trust: float = 0.9,
        input_size: tuple[int, int] = (256, 128),   # (h, w) — ReID default
        device: str | None = None,
        mask_background: bool = False,
        builtin_norm: bool = False,
        mean: tuple[float, float, float] = _IMAGENET_MEAN,
        std: tuple[float, float, float] = _IMAGENET_STD,
    ) -> None:
        self.model_path = Path(model_path)
        self.model_id = model_id
        self.trust = float(trust)
        self._size = input_size
        self._device = device
        self._mask_bg = mask_background
        # builtin_norm=True: the scripted model normalizes internally (e.g. fast-reid,
        # which expects raw [0,255] RGB). We then skip our own /255 + mean/std.
        self._builtin_norm = bool(builtin_norm)
        self._mean = np.array(mean, dtype=np.float32).reshape(1, 1, 3)
        self._std = np.array(std, dtype=np.float32).reshape(1, 1, 3)
        self._model = None
        self._torch = None
        self._loaded = False
        self._ok = False
        self.dim = 0

    def _load(self) -> bool:
        if self._loaded:
            return self._ok
        self._loaded = True
        try:
            import torch
            if not self.model_path.exists():
                return False
            # Prefer deterministic cuDNN kernels so the same crop yields the same vector
            # run-to-run (GPU conv algorithm selection is otherwise nondeterministic).
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
            device = self._device or ("cuda" if torch.cuda.is_available() else "cpu")
            self._torch = torch
            self._device = device
            self._model = torch.jit.load(str(self.model_path), map_location=device).eval()
            # Warm up once so cuDNN's first-call algorithm selection happens now, not on
            # the first real query — after this, repeated encodes of the same crop are
            # byte-stable (the first-inference artifact is the only GPU nondeterminism).
            h, w = self._size
            with torch.no_grad():
                self._model(torch.zeros(1, 3, h, w, device=device))
            self._ok = True
        except Exception:  # noqa: BLE001 - any failure => unavailable, engine falls back
            self._ok = False
        return self._ok

    def available(self) -> bool:
        return self._load()

    def _preprocess(self, crops: list[np.ndarray],
                    masks: list[np.ndarray | None] | None) -> np.ndarray:
        h, w = self._size
        batch = []
        for i, crop in enumerate(crops):
            c = apply_mask(crop, masks[i]) if (self._mask_bg and masks) else crop
            resized = cv2.resize(c, (w, h))
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32)
            if self._builtin_norm:
                pass                              # model normalizes internally; keep [0,255]
            else:
                rgb = (rgb / 255.0 - self._mean) / self._std
            batch.append(rgb.transpose(2, 0, 1))
        return np.stack(batch)

    def encode(self, crops: list[np.ndarray],
               masks: list[np.ndarray | None] | None = None) -> np.ndarray:
        if not self.available() or not crops:
            return np.empty((0, self.dim), dtype=np.float32)
        tensor = self._torch.from_numpy(self._preprocess(crops, masks)).to(self._device)
        with self._torch.no_grad():
            out = self._model(tensor)
        vecs = out.detach().cpu().numpy().astype(np.float32)
        if vecs.ndim > 2:
            vecs = vecs.reshape(vecs.shape[0], -1)
        self.dim = int(vecs.shape[1])
        # Round after normalizing so the returned vector is byte-stable across runs (any
        # residual GPU float noise at ~1e-6 is quantized away); cosine is unaffected at
        # this precision, and identical inputs now give identical outputs.
        return np.round(l2_normalize(vecs), 6)
