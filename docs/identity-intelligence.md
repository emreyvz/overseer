# Identity intelligence: gait re-ID, long-term dossiers, multi-frame reconstruction

Three linked capabilities that give surveillance identity a longer memory and a sharper eye. They
share one backbone: a persistent gallery of descriptors + a sighting log per subject.

- **Feature 5 - gait + soft biometrics:** identify a person by body shape and how they walk, which
  survives clothing changes and face occlusion.
- **Feature 6 - long-term repeat-visitor dossiers:** recognize the same subject across days and
  weeks, and show their whole history (first/last seen, visit count, per-camera and time-of-day
  patterns, a repeat-visitor flag).
- **Feature 7 - multi-frame super-resolution:** fuse many crops of one subject or plate into a
  single sharper, higher-resolution image.

They live inside the roster **profile page** (`PoiDossier.svelte`): open any person/vehicle from the
roster, and the long-term identity panel (repeat-visit pattern, gait / soft-biometric profile, and a
CLARIFY PHOTO reconstruction button) sits under a shrunk movement trace. Double-clicking a node in
the profile's relationship network navigates to that subject's profile with an animated transition.

## Backbone (feature 6): persistent identity

The live session roster (`server/roster.py`) dedups identities within one run but is RAM-only. A
new persistent layer gives it a durable memory:

- **Schema** (`storage/database.py`): `subjects` (durable cross-session identity + counts + flags),
  `subject_descriptors` (a capped gallery of many descriptors per subject, `appearance` and `gait`
  kinds), `sightings` (an append-only log with camera + timestamp + crop).
- **`server/identity_store.py` `SubjectStore`:** on each sighting it cosine-matches the appearance
  embedding (and gait descriptor, when present) against the persisted gallery within a recent
  window; a match folds in and logs a sighting, otherwise a new subject is created. Recognition
  therefore averages over many crops across days rather than one best crop. A small in-memory
  gallery cache keeps matching fast.
- **Wiring:** `SessionRoster.observe_reid` calls `SubjectStore.record(...)`, fully guarded and
  throttled so it can never slow or break the roster. Off via config `roster.persist: false`.
- **Dossier:** `Database.subject_dossier` aggregates per-camera counts, an hour-of-day histogram,
  and distinct-day counts straight from `sightings`. A subject seen on >= 3 distinct days is
  flagged `repeat_visitor`.

## Feature 5: gait + soft biometrics

- **`server/gait.py`:** `gait_descriptor(seq)` turns a track's COCO-17 pose sequence into a compact
  L2-normalized vector combining clothing-invariant limb-length ratios (a body-shape signature) and
  gait dynamics (step cadence in Hz, stride amplitude, vertical bounce, arm swing). Features are
  centered against rough population nominals and winsorized so no single miscalibrated measurement
  dominates. Static ratios are always produced; gait dynamics are added only when the legs are
  visible across enough frames (else left neutral), so an occluded-leg subject still gets a body
  descriptor.
- **`GaitTracker`:** accumulates per-track skeletons (matched to person boxes by IoU) and emits a
  descriptor once a track has walked enough frames.
- **Wiring:** `PoseKP.detect_pose` returns raw skeletons (one inference now feeds both the existing
  hand-raise behaviour and gait). In `Backend._on_result` the active camera's tracked persons feed
  the tracker; a ready descriptor is persisted into the identity store with its appearance embedding
  so the two **fuse** in matching. Off via config `gait.enabled: false`.
- The gait descriptor makes cross-day recognition (feature 6) robust to a change of clothes, and the
  dossier surfaces the soft-biometric profile (build ratio, leg/torso ratio, cadence).

## Feature 7: multi-frame super-resolution

- **`server/reconstruct.py`:** `reconstruct(crops)` ranks crops by sharpness, Lanczos-upscales,
  aligns each to the sharpest with sub-pixel ECC (run on capped-resolution crops for speed, the
  affine then scaled to the output), and robustly median-fuses the well-aligned ones (a median
  rejects occluders and cuts sensor noise by ~sqrt(N)). The output is finalized with edge-preserving
  denoise + CLAHE local contrast + multi-scale unsharp. Frames that do not align above a correlation
  floor are rejected, so **the result is never worse than a good zoom**: a burst of near-identical
  crops (a plate across frames) genuinely fuses (`method: multiframe`), while disparate crops fall
  back to a strong single-frame enhance (`method: single`). A learned face/plate super-resolution
  model (GFPGAN / Real-ESRGAN) would be the next step for dramatic gains, at the cost of a download.
- **Crop sources:** plate crops are buffered per track in `LivePlateReader` (previously read once and
  thrown away) and re-OCR'd after fusion; a subject's distinct sighting crops feed subject
  reconstruction.
- **Endpoints:** `GET /api/subjects/{id}/reconstruct`, `GET /api/reconstruct/plate/{det_id}`.
- **UI:** the RECONSTRUCT button on a dossier.

## HTTP endpoints

- `GET /api/subjects?cls=&limit=&order=` - persisted subjects, most recent first.
- `GET /api/subjects/{id}/dossier` - `{dossier: {... per_camera, hour_histogram, distinct_days, sightings}}`.
- `GET /api/subjects/{id}/reconstruct` - `{image(b64)|null, method, frames_used, frames_offered}`.
- `GET /api/reconstruct/plate/{det_id}` - same shape, plus `plate` when a fused read succeeds.

## Config

- `roster.persist` (default true) - persist subjects/sightings; `roster.persist_threshold` (0.74).
- `gait.enabled` (default true) - accumulate gait/soft-biometrics on the active camera.

## Tests

- `tests/test_reconstruct.py` - fusion beats a single noisy frame, ECC recovers shifts, guards.
- `tests/test_identity_store.py` - cross-day recognition, gait fusion separates look-alikes, gallery
  cap, subject merge.
- `tests/test_gait.py` - same body matches / different bodies separate, scale invariance, cadence
  recovery, graceful without legs, the `GaitTracker` accumulation path.

## Verified live

On the running backend: 200+ subjects persisted with dossiers; gait descriptors + soft-biometric
attrs recorded from the active camera's tracked people; reconstruction returns valid enhanced/fused
images. Build + type-check clean.

## Honest limits

- Gait dynamics (cadence/stride) need visible, moving legs; occluded-leg subjects fall back to the
  static body-shape descriptor. Distant/tiny people yield no confident skeleton.
- Multi-frame fusion only super-resolves near-identical consecutive crops (plates, a burst of one
  track); disparate crops are single-frame enhanced instead.
- Recognition matching is O(recent gallery) per sighting; fine at demo scale, and a real deployment
  would add an ANN index for very large galleries.
