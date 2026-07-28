"""Datasets for the eval harness: a deterministic synthetic generator (no external data,
no RNG — reproducible everywhere) plus loaders for real labeled manifests that drop into
the same shape to measure real model weights."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# Six visually distinct BGR base colours. Each identity is a top/bottom pair of these,
# so identities are separable by a colour+structure encoder.
_PALETTE = [
    (60, 60, 220),    # red
    (220, 120, 40),   # blue
    (40, 200, 60),    # green
    (40, 200, 220),   # yellow
    (200, 60, 200),   # magenta
    (200, 200, 40),   # cyan
]


@dataclass(frozen=True)
class LabeledCrop:
    crop: np.ndarray
    label: str


def _identity_crop(idx: int, brightness: float, h: int = 96, w: int = 48) -> np.ndarray:
    top = np.array(_PALETTE[idx % len(_PALETTE)], dtype=np.float32)
    bot = np.array(_PALETTE[(idx + 2) % len(_PALETTE)], dtype=np.float32)
    img = np.zeros((h, w, 3), dtype=np.float32)
    img[: h // 2] = top
    img[h // 2:] = bot
    return np.clip(img * brightness, 0, 255).astype(np.uint8)


def synthetic_reid(n_ids: int = 6, gallery_per_id: int = 3
                   ) -> tuple[list[LabeledCrop], list[LabeledCrop]]:
    """Return (queries, gallery). One query per identity; ``gallery_per_id`` brightness
    variants of each identity in the gallery. Deterministic."""
    brightness = [0.75, 1.0, 1.25]
    queries: list[LabeledCrop] = []
    gallery: list[LabeledCrop] = []
    for i in range(n_ids):
        label = f"id{i:02d}"
        queries.append(LabeledCrop(_identity_crop(i, 1.0), label))
        for j in range(gallery_per_id):
            b = brightness[j % len(brightness)]
            gallery.append(LabeledCrop(_identity_crop(i, b), label))
    return queries, gallery


def load_reid_manifest(path: str | Path) -> tuple[list[LabeledCrop], list[LabeledCrop]]:
    """Load a real ReID manifest: JSON list of {file, id, role} where role is
    'query'|'gallery'. Paths are resolved relative to the manifest's directory."""
    path = Path(path)
    rows = json.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    queries: list[LabeledCrop] = []
    gallery: list[LabeledCrop] = []
    for row in rows:
        img = cv2.imread(str(base / row["file"]))
        if img is None:
            continue
        lc = LabeledCrop(img, str(row["id"]))
        (queries if row.get("role") == "query" else gallery).append(lc)
    return queries, gallery


@dataclass(frozen=True)
class AnprCase:
    reads: list[tuple[str, float]]     # per-frame (text, ocr_conf)
    plate: str                          # ground-truth plate


def load_anpr_fixture(path: str | Path) -> list[AnprCase]:
    """Load ANPR cases: JSON list of {reads: [[text, conf], ...], plate: str}."""
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return [AnprCase([(str(t), float(c)) for t, c in row["reads"]], str(row["plate"]))
            for row in rows]


DEFAULT_ANPR_FIXTURE = Path(__file__).parent / "fixtures" / "anpr_reads.json"
