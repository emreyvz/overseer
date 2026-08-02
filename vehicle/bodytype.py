"""Vehicle body-type from a crop via zero-shot CLIP (sedan / hatchback / SUV / ...).

COCO only tells us car vs truck vs bus vs motorcycle; operators want the finer body shape. A
fine-tuned body-type classifier would be US-market and date badly, so this uses CLIP zero-shot
instead: the crop is scored against a fixed set of body-type text prompts (prompt-ensembled for
stability) and the best label is returned only when it is both confident AND clearly ahead of the
runner-up. On real CCTV crops this reads a Yaris as a hatchback, an MPV as a minibus, a bus as a
bus; when it is unsure it returns nothing rather than a confident-but-wrong shape.

CLIP is loaded through transformers (already a core dependency for the depth model) and its weights
fetch lazily from the Hugging Face hub on first use, exactly like Depth Anything. Everything is
best-effort and lazy: if transformers or the model is unavailable, ``available()`` is False and the
feature simply goes quiet, like the make classifier and the ReID encoders.

Worker/harvester-thread only (a GPU encode is ~10 ms; a CPU encode is throttled per track).
"""
from __future__ import annotations

import os
import threading

import cv2
import numpy as np

# Display label -> the noun phrase CLIP scores against. Order is irrelevant; the gate picks the best.
_TYPES: dict[str, str] = {
    "sedan": "sedan car",
    "hatchback": "hatchback car",
    "SUV": "SUV",
    "station wagon": "station wagon estate car",
    "coupe": "two-door coupe car",
    "pickup": "pickup truck",
    "minivan": "minivan people carrier",
    "van": "cargo van",
    "minibus": "minibus",
    "bus": "bus",
    "truck": "large truck lorry",
    "motorcycle": "motorcycle",
    "bicycle": "bicycle",
}
# Prompt ensemble: averaging a few phrasings is markedly steadier than a single template.
_TEMPLATES = ("a photo of a {}.", "a {} seen from a street security camera.", "a {} parked on the street.")

_MODEL_ID = os.environ.get("OVERSEER_CLIP_MODEL", "openai/clip-vit-base-patch32")


def _pick(probs: np.ndarray, labels: list[str], min_conf: float,
          min_margin: float) -> tuple[str, float] | None:
    """Confidence-gated top-1: (label, prob) only when confident AND decisive; else None."""
    if probs.size == 0 or probs.size != len(labels):
        return None
    order = np.argsort(probs)[::-1]
    top = int(order[0])
    p1 = float(probs[top])
    p2 = float(probs[int(order[1])]) if probs.size > 1 else 0.0
    if p1 < min_conf or (p1 - p2) < min_margin:
        return None
    return labels[top], round(p1, 3)


class BodyTypeClassifier:
    def __init__(self, *, min_conf: float = 0.35, min_margin: float = 0.15,
                 min_area: int = 9000, device: str | None = None) -> None:
        self._min_conf = float(min_conf)
        self._min_margin = float(min_margin)
        self._min_area = int(min_area)
        self._device = device
        self._labels: list[str] = list(_TYPES)
        self._model = None
        self._proc = None
        self._torch = None
        self._text = None            # (num_labels, dim) normalized text embeddings
        self._loaded = False
        self._ok = False
        self._lock = threading.Lock()

    def _load(self) -> bool:
        if self._loaded:
            return self._ok
        self._loaded = True
        try:
            import torch
            from transformers import CLIPModel, CLIPProcessor
            device = self._device or ("cuda" if torch.cuda.is_available() else "cpu")
            model = CLIPModel.from_pretrained(_MODEL_ID).to(device).eval()
            proc = CLIPProcessor.from_pretrained(_MODEL_ID)
            # Precompute the ensembled, normalized text prototype per body type (once).
            prompts, spans = [], []
            for noun in _TYPES.values():
                start = len(prompts)
                prompts += [t.format(noun) for t in _TEMPLATES]
                spans.append((start, len(prompts)))
            with torch.no_grad():
                tin = proc(text=prompts, return_tensors="pt", padding=True).to(device)
                tf = model.get_text_features(**tin)
                if not torch.is_tensor(tf):                 # transformers 5.x returns an output obj
                    tf = getattr(tf, "text_embeds", getattr(tf, "pooler_output"))
                tf = tf / tf.norm(dim=-1, keepdim=True)
                proto = torch.stack([tf[a:b].mean(0) for a, b in spans])
                proto = proto / proto.norm(dim=-1, keepdim=True)
            self._torch = torch
            self._device = device
            self._model = model
            self._proc = proc
            self._text = proto
            self._ok = True
        except Exception:  # noqa: BLE001 - any failure => unavailable, feature goes quiet
            self._ok = False
        return self._ok

    def available(self) -> bool:
        return self._load()

    def _probs(self, crop: np.ndarray) -> np.ndarray:
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        with self._torch.no_grad():
            iin = self._proc(images=rgb, return_tensors="pt").to(self._device)
            jf = self._model.get_image_features(**iin)
            if not self._torch.is_tensor(jf):
                jf = getattr(jf, "image_embeds", getattr(jf, "pooler_output"))
            jf = jf / jf.norm(dim=-1, keepdim=True)
            sim = (jf @ self._text.T)[0]
            p = self._torch.softmax(sim * 100.0, dim=-1)
        return p.detach().cpu().numpy().astype(np.float32)

    def classify(self, crop: np.ndarray | None) -> tuple[str, float] | None:
        """Stateless, confidence-gated body type for one crop; None if unavailable/uncertain/tiny.
        Matches MakeClassifier.classify's signature so it can share the live reader plumbing."""
        if crop is None or getattr(crop, "size", 0) == 0 or not self.available():
            return None
        if crop.shape[0] * crop.shape[1] < self._min_area:
            return None
        try:
            with self._lock:
                probs = self._probs(crop)
        except Exception:  # noqa: BLE001
            return None
        return _pick(probs, self._labels, self._min_conf, self._min_margin)
