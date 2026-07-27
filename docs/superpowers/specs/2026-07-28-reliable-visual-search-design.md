# Reliable, Verifiable Visual & Object Search — Design

**Date:** 2026-07-28
**Status:** Approved (approach A), auto-mode implementation
**Author:** Emre Yavuz + Claude

## Problem

The current forensic and object search paths are neither reliable nor verifiable:

1. **Forensic text search** (`/api/search` → `ForensicSearchService`) is deterministic as a
   SQLite filter, but the *stored attributes it queries* are camera/pose/lighting-dependent
   heuristics:
   - `height_band` = `bbox_height / frame_height` → depends on distance to camera, not the person.
   - `build` = bbox aspect ratio → depends on pose, occlusion, box tightness.
   - `upper/lower_color` = dominant HSV over the **whole crop** (background included) → colour
     contaminated by wall/sky/ground.
   - `clothing_type` = `ModelAttributes.classify` with `labels[i % len(labels)]` → wraps to an
     arbitrary label if the model's output dim ≠ label count; and the attribute model is never
     constructed (always `None`).
   - `attr_conf` is always `1.0`; attributes come from a **single** frame — no temporal voting.

2. **Object / visual match** (`/api/visualmatch` → `backend.visual_match`):
   - **Live-only**: it reads each source's *current* frame, so the "same" query returns different
     results on every call → feels non-deterministic.
   - Matching = **HS colour-histogram correlation**, not identity → two different people in
     similarly coloured clothes match.
   - The real identity path (`ReidEmbedder`, cosine over embeddings, `MetadataIndex.find_similar`)
     exists but is **dead code**: no encoder is constructed, no weights ship, no endpoint calls it.
   - Magic thresholds (0.42, margin 0.06) with no calibration.

## Goals (from brainstorming)

- **Real identity accuracy** for **people and vehicles** (not colour similarity).
- **ANPR** (multi-country / generic — no country format binding) so vehicle identity can be
  definitive when a plate is read.
- **Professional visual similarity** for generic objects/animals (learned embeddings, not colour).
- **Live-only but stable**: search current cameras, but aggregate over the last N seconds so a
  single noisy frame can't swing the result, and produce a stable, reproducible score.
- **Verifiable**: a full evaluation harness (labeled fixtures + metrics), deterministic golden
  tests in pytest, and per-hit **evidence** (crop, score components, confidence, margin, model id)
  surfaced through the API/UI.

Non-goals: persistent historical vector index / cross-time search; retraining models.

## Approach A — per-class encoders behind one interface

A new `match/` package built around a **deterministic scoring contract** and **pluggable model
backends**. The heavy models are backends that drop in behind stable interfaces; the pipeline,
scoring, ANPR normalization, aggregation and metrics are **pure Python** and fully unit-testable
without any model download. When weights are present the engine uses them; when they are not, it
falls back to a clearly-labeled deterministic baseline encoder and reports lower trust — it never
silently pretends.

### Modules

```
match/
  types.py          # Candidate, Query, Embedding, Evidence, MatchHit, MatchResult (frozen dataclasses)
  scoring.py        # PURE: cosine, window aggregation, margin, ambiguity, confidence calibration
  rolling.py        # RollingFrameStore: per-source ring buffer of recent frames (last N s)
  segmentation.py   # Segmenter: foreground mask for a crop (YOLO-seg backend | deterministic fallback)
  engine.py         # MatchEngine.match(query, sources) -> MatchResult  (no I/O; deterministic given encoders)
  encoders/
    base.py         # Encoder ABC: model_id, trust, available(), encode(crops, masks) -> (N,D) L2-normed
    baseline.py     # DeterministicEncoder: stable aHash-style embedding (tests + graceful fallback)
    reid_person.py  # PersonReidEncoder (OSNet/torchreid TorchScript), lazy load
    reid_vehicle.py # VehicleReidEncoder (VeRi-trained), lazy load
    generic.py      # GenericEncoder (DINOv2/CLIP image encoder), lazy load
  anpr/
    normalize.py    # PURE: normalize_plate, plate_similarity (normalized Levenshtein), plates_match
    voting.py       # PURE: PlateVoter — temporal voting over per-frame reads -> (plate, confidence)
    reader.py       # PlateReader: detect+OCR backend (PaddleOCR/EasyOCR), lazy load
  eval/
    metrics.py      # PURE: cmc_rank_k, mean_ap, anpr_exact_match, precision_recall_at
    dataset.py      # load labeled manifest; deterministic synthetic generator for CI
    runner.py       # CLI: run encoder over dataset, emit metrics report (json)
    fixtures/       # small synthetic dataset + manifest committed to repo
```

### The scoring contract (`scoring.py`, pure)

Given a query embedding `q` and, per source, per-frame candidate embeddings for same-class
detections:

1. **Per-frame score** = cosine(q, v), v L2-normalized → deterministic, in [-1, 1].
2. **Window aggregation** over the last N seconds for a given track/candidate:
   `aggregate(scores)` = high-percentile (trimmed) score, valid only if at least
   `min_temporal_support` frames exceed `frame_floor`. This is the "stable" requirement: one lucky
   frame is not enough; the match must persist across frames.
3. **Margin** = best_source_score − runner_up_source_score. `ambiguous = margin < min_margin`.
4. **Confidence calibration** (deterministic map to [0,1]) from: aggregated cosine, margin,
   detector confidence, mask coverage, encoder `trust` (baseline < specialized), temporal support,
   and (vehicles) plate agreement. Documented, monotonic, unit-tested against golden values.
5. **Definitive plate short-circuit** (vehicles): if the query carries a plate (or a plate is read
   for a candidate) and `plates_match(query_plate, cand_plate)`, confidence is set to the
   plate-definitive ceiling regardless of appearance cosine; the plate string is the identity.

All thresholds live in one `ScoringConfig` dataclass (sourced from `core.config`), so the contract
is inspectable and reproducible.

### Encoders (`encoders/`)

- One interface: `encode(crops, masks) -> (N, D) float32, L2-normalized`, plus `model_id`,
  `trust ∈ (0,1]`, `available()`. Masks (from `Segmenter`) zero the background before encoding so a
  red jacket isn't diluted by a green wall.
- `DeterministicEncoder` (baseline, `trust≈0.35`): resize to a fixed small gray grid, flatten,
  mean-center, L2-normalize → a stable, download-free embedding that carries *some* real signal.
  It makes the whole engine and its tests runnable offline and deterministic, and its low trust
  caps confidence so results are honestly labeled "baseline".
- Real encoders lazy-load weights via the existing `ModelManager.ensure_model` download pattern;
  on failure they report `available() == False` and the engine falls back to baseline with a
  warning in `MatchResult.warnings`.

### ANPR (`anpr/`)

- `normalize.py` / `voting.py` are pure: uppercase, strip non-alphanumerics, optional confusable
  folding (O↔0, I↔1) behind a flag, normalized-Levenshtein similarity, and temporal voting that
  only asserts a plate seen consistently across ≥K frames with aggregate OCR confidence. Multi-
  country: no country regex; verifiability comes from **temporal consistency + OCR confidence**,
  not a format rule.
- `reader.py` wraps the OCR backend behind `available()`; absent → vehicles fall back to ReID
  appearance only.

### Engine (`engine.py`)

`MatchEngine.match(query, sources)`:
1. Encode the query crop once (with its mask).
2. For each source, over its rolling-window frames: detect same-class candidates → segment →
   encode → cosine → aggregate over the window → keep the source's best candidate + runner-up for
   margin. Vehicles additionally run ANPR + plate voting.
3. Build `MatchHit`s with full `Evidence`; sort by `(score desc, source_id asc)` — **stable,
   deterministic ordering**.
The engine performs **no I/O**: it receives already-captured frames and a detector callable, so it
is fully deterministic given (fake) encoders — this is what the golden tests exercise.

### Rolling store (`rolling.py`)

`RollingFrameStore` keeps a per-source ring buffer of the last N seconds of analysed frames. The
backend feeds frames in as they're analysed; a query reads the window. This delivers "live but
stable" without any persistent index.

### Backend wiring (`server/backend.py`)

- `Backend.__init__` constructs a `MatchEngine` with lazy encoders + segmenter + plate reader and a
  `RollingFrameStore`; the analysis loop pushes frames into the store.
- `visual_match` is rewritten to: infer/accept the query class, pull each source's window, call
  `engine.match`, and map hits to the **existing** `/api/visualmatch` response shape **plus** new
  fields: `confidence`, `plate`, `evidence` (score components + model id). Backward compatible.

### Verification (`eval/` + pytest)

- **Golden deterministic tests**: engine + scoring + ANPR normalization/voting + metrics, all run
  with the baseline encoder / synthetic data → same input, same output, in CI.
- **Eval harness**: `metrics.py` computes CMC rank-k and mAP for ReID encoders, exact-match
  accuracy for ANPR, precision/recall at threshold. `runner.py` runs an encoder over a labeled
  manifest and emits a JSON report. A committed **synthetic fixture** (deterministic colored/pattern
  identities + fake plates) proves the harness end-to-end without external datasets; real datasets
  drop into the same manifest format to measure real weights.
- **Per-hit evidence** in the API response makes each live result self-explaining.

### Secondary: forensic attribute fix

Since forensic *text* search was also named, `forensic/attributes.py` + `tracklet` sampling are
improved to: compute colour on the **segmented** foreground (torso/legs bands), aggregate
attributes by **temporal voting** across a tracklet's samples (mode + agreement ratio → real
`attr_conf`), and remove the `labels[i % len]` clothing bug (wire a real PAR model behind
`available()` or store `None` honestly). Search can then filter by `attr_conf` floor.

## Testing strategy

- Pure modules (`scoring`, `anpr/normalize`, `anpr/voting`, `eval/metrics`): exhaustive unit tests
  incl. golden values and edge cases (empty, ties, single-frame, all-background mask).
- `engine`: integration tests with the deterministic encoder and hand-built synthetic multi-source
  frames asserting the correct source wins, margin/ambiguity flags, and plate short-circuit.
- `eval/runner`: a test runs it over the committed synthetic fixture and asserts rank-1 == 1.0 for
  a perfect-recall synthetic set and expected ANPR exact-match.
- Existing suite (`uv run pytest`) stays green; `npm run build` stays green.

## Rollout order

1. Pure core: `types`, `scoring`, `anpr/normalize`, `anpr/voting`, `eval/metrics` + tests.
2. `encoders/base` + `baseline`, `segmentation` (deterministic fallback) + tests.
3. `engine` + `rolling` + integration tests.
4. `eval/dataset` + synthetic `fixtures` + `runner` + test.
5. Real encoder/segmenter/OCR adapters (lazy, behind `available()`).
6. Backend wiring + API evidence fields + config keys.
7. Forensic attribute improvements + tests.
