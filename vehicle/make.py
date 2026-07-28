"""Vehicle make (brand) classification from a crop, backed by a TorchScript ViT.

The scripted model is a ViT fine-tuned to 55 car brands (see match.tools.export_models:carbrand).
Make-level is the honest target — specific make+model+year classifiers are US-market and date
badly, whereas brand generalises and the 55-way set covers the makes common on European/Turkish
roads (Renault, Fiat, Peugeot, Citroen, VW, Hyundai, Kia, ...).

Two guards keep it from asserting nonsense on the many things that aren't clean passenger-car
shots (buses, trucks seen from odd angles, tiny far crops): a size floor, and a confidence gate
that needs both a high top probability AND a clear margin over the runner-up — otherwise it
returns nothing rather than a confident-but-wrong brand. Everything heavy is lazy and guarded:
a missing weight file or torch import makes ``available()`` False and the feature simply goes
quiet, exactly like the ReID encoders.

Worker/harvester-thread only.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np


def _pick(probs: np.ndarray, labels: list[str], min_conf: float,
          min_margin: float) -> tuple[str, float] | None:
    """Confidence-gated top-1: return (label, prob) only when the model is both confident and
    decisive; None otherwise. Pure and deterministic — the unit-tested core of the gate."""
    if probs.size == 0 or probs.size != len(labels):
        return None
    order = np.argsort(probs)[::-1]
    top = int(order[0])
    p1 = float(probs[top])
    p2 = float(probs[int(order[1])]) if probs.size > 1 else 0.0
    if p1 < min_conf or (p1 - p2) < min_margin:
        return None
    return labels[top], round(p1, 3)


class MakeClassifier:
    def __init__(self, model_path: str | Path, labels_path: str | Path | None = None, *,
                 min_conf: float = 0.35, min_margin: float = 0.10, interval: float = 4.0,
                 min_area: int = 3600, device: str | None = None) -> None:
        self.model_path = Path(model_path)
        self._labels_path = Path(labels_path) if labels_path else Path(
            str(self.model_path).rsplit(".", 1)[0] + ".labels.json")
        self._min_conf = float(min_conf)
        self._min_margin = float(min_margin)
        self._interval = float(interval)
        self._min_area = int(min_area)
        self._device = device
        self._labels: list[str] = []
        self._model = None
        self._torch = None
        self._loaded = False
        self._ok = False
        self._cache: dict[int, tuple[str | None, float]] = {}   # track_id -> (make, ts)

    def _load(self) -> bool:
        if self._loaded:
            return self._ok
        self._loaded = True
        try:
            import torch
            if not self.model_path.exists() or not self._labels_path.exists():
                return False
            self._labels = json.loads(self._labels_path.read_text(encoding="utf-8"))
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
            # CPU by default: the ViT is traced on CPU (HF bakes some constants to the trace
            # device, so loading it onto CUDA hits a device mismatch), and running it on CPU
            # keeps make classification off the GPU hot path. It only runs in the background
            # roster harvester, throttled per vehicle, so CPU latency is a non-issue there.
            device = self._device or "cpu"
            self._torch = torch
            self._device = device
            self._model = torch.jit.load(str(self.model_path), map_location=device).eval()
            with torch.no_grad():
                self._model(torch.zeros(1, 3, 224, 224, device=device))   # warm up
            self._ok = True
        except Exception:  # noqa: BLE001 - any failure => unavailable, feature goes quiet
            self._ok = False
        return self._ok

    def available(self) -> bool:
        return self._load()

    def _probs(self, crop: np.ndarray) -> np.ndarray:
        resized = cv2.resize(crop, (224, 224))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        rgb = (rgb - 0.5) / 0.5                                    # ViT: mean/std = 0.5
        tensor = self._torch.from_numpy(rgb.transpose(2, 0, 1)[None]).to(self._device)
        with self._torch.no_grad():
            logits = self._model(tensor)
        e = self._torch.softmax(logits, dim=1)[0]
        return e.detach().cpu().numpy().astype(np.float32)

    def classify(self, crop: np.ndarray | None) -> tuple[str, float] | None:
        """Stateless, confidence-gated brand for one crop; None if unavailable/uncertain/tiny."""
        if crop is None or getattr(crop, "size", 0) == 0 or not self.available():
            return None
        if crop.shape[0] * crop.shape[1] < self._min_area:
            return None
        try:
            probs = self._probs(crop)
        except Exception:  # noqa: BLE001
            return None
        return _pick(probs, self._labels, self._min_conf, self._min_margin)

    def make_for(self, track_id: int, crop: np.ndarray | None, now: float) -> str | None:
        """Throttled per-track brand: classifies at most once per `interval` for a given track
        and caches the result (keeping the last confident answer across uncertain frames), so
        the live card stays stable and the ViT isn't run every frame on every vehicle."""
        c = self._cache.get(track_id)
        if c is not None and now - c[1] < self._interval:
            return c[0]
        hit = self.classify(crop)
        label = hit[0] if hit else (c[0] if c else None)   # keep last good label on an uncertain frame
        self._cache[track_id] = (label, now)
        return label

    def prune(self, active_ids: set[int]) -> None:
        for tid in [t for t in self._cache if t not in active_ids]:
            del self._cache[tid]

    def reset(self) -> None:
        self._cache.clear()
