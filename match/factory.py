"""Assemble a MatchEngine from Config. Specialized encoders load when their weights are
present under ``models_dir``; otherwise the always-available deterministic baseline is
used and that choice is reported in the returned ``info`` (and surfaced as a warning by the
backend), so degradation is visible, never silent."""
from __future__ import annotations

from pathlib import Path

from match.anpr.reader import OcrPlateReader
from match.anpr.voting import PlateVoter
from match.encoders.baseline import DeterministicEncoder
from match.encoders.base import Encoder
from match.encoders.resolve import best_available
from match.encoders.torchscript import TorchScriptEncoder
from match.engine import MatchEngine
from match.scoring import ScoringConfig
from match.seg_backend import YoloSegBackend
from match.segmentation import Segmenter

_TRUST = {"person_reid": 0.92, "vehicle_reid": 0.90, "generic": 0.80}


def _resolve(config, key: str, size_key: str, models_dir: Path,
             baseline: Encoder, *, mask_bg: bool) -> tuple[Encoder, bool]:
    name = str(config.get(f"match.models.{key}", "") or "")
    candidates: list[Encoder] = []
    if name:
        size = tuple(int(v) for v in config.get(f"match.{size_key}", [256, 128]))
        candidates.append(TorchScriptEncoder(
            models_dir / name, Path(name).stem, trust=_TRUST.get(key, 0.85),
            input_size=size, mask_background=mask_bg,
        ))
    return best_available(candidates, baseline)


def build_engine(config, detect, *, models_dir: str | Path = "models",
                 category_to_cls: dict[str, str] | None = None
                 ) -> tuple[MatchEngine, dict]:
    models_dir = Path(models_dir)
    baseline = DeterministicEncoder()

    # generic embedder (DINOv2/CLIP) reads whole-image semantics -> do not mask
    generic, _fb_g = _resolve(config, "generic", "generic_input", models_dir,
                              baseline, mask_bg=False)
    # When no dedicated ReID weights are present, fall back to the generic embedder if it
    # loaded (far stronger than the colour-grid baseline) and only then to baseline.
    reid_fallback = generic if generic.available() else baseline
    person, _fb_p = _resolve(config, "person_reid", "reid_input", models_dir,
                             reid_fallback, mask_bg=True)
    vehicle, _fb_v = _resolve(config, "vehicle_reid", "vehicle_input", models_dir,
                              reid_fallback, mask_bg=True)

    seg_name = str(config.get("match.models.seg", "") or "")
    seg_backend = YoloSegBackend(models_dir / seg_name) if seg_name else None
    segmenter = Segmenter(backend=seg_backend)

    anpr_on = bool(config.get("match.anpr.enabled", True))
    plate_reader = OcrPlateReader(
        langs=tuple(config.get("match.anpr.langs", ["en"])),
        gpu=bool(config.get("match.anpr.gpu", True)),
    ) if anpr_on else None
    voter = PlateVoter(min_agreement=int(config.get("match.anpr.min_agreement", 3)))

    scoring = ScoringConfig(
        frame_floor=float(config.get("match.scoring.frame_floor", 0.5)),
        min_temporal_support=int(config.get("match.scoring.min_temporal_support", 2)),
        aggregate_percentile=float(config.get("match.scoring.aggregate_percentile", 80.0)),
        accept_threshold=float(config.get("match.scoring.accept_threshold", 0.6)),
        min_margin=float(config.get("match.scoring.min_margin", 0.06)),
    )

    engine = MatchEngine(
        encoders={"person": person, "vehicle": vehicle,
                  "animal": generic, "object": generic},
        detect=detect, segmenter=segmenter, scoring=scoring,
        plate_reader=plate_reader, plate_voter=voter,
        plate_fold=bool(config.get("match.anpr.fold_confusable", True)),
        category_to_cls=category_to_cls,
    )
    info = {
        "person": person.model_id,
        "vehicle": vehicle.model_id,
        "generic": generic.model_id,
        "segmenter": "yolo-seg" if (seg_backend and seg_backend.available()) else "ellipse",
        "anpr": "on" if (plate_reader and plate_reader.available()) else "off",
    }
    return engine, info
