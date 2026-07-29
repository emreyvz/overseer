# Feature 4 — Spatial 3D Scene View (monocular depth)

## Goal (re-scoped by the operator)
Not a photorealistic 3D copy of the world. The goal is to let the operator **think in 3D
about what a single camera sees** — to lift a flat 2D feed into a navigable spatial
reconstruction, so distances, layering and where each person/vehicle stands in space become
legible. Works on any scene the camera happens to show: an airport concourse, an apartment
street, a shop floor.

## Method — new-generation monocular depth
A single RGB frame is enough. **Depth Anything V2 (Small)** — a 2024 DINOv2-backbone depth
transformer — estimates a dense per-pixel relative-depth map from one image, with no stereo,
no LiDAR, no calibration. Runs in ~30 ms on the existing CUDA torch stack. The depth map is
then **back-projected** through a pinhole camera model into a coloured 3D point cloud the
operator can orbit.

## Architecture

### Backend
- `server/depth.py` — `DepthEstimator`: lazy singleton around Depth Anything V2 via
  `transformers`. `estimate(bgr) -> float32 disparity | None`. Thread-safe (a lock; the GPU is
  shared with the harvester). Config-gated; returns `None` when disabled or the model/weights
  are unavailable, so the feature degrades to "unavailable", never a 5xx.
- `Backend.spatial_scene(sid, grid) -> dict | None`:
  1. grab the latest BGR frame for the source (`_source_frame`),
  2. run depth → disparity,
  3. run the detector on that same frame so entity boxes align exactly with the point cloud,
  4. downscale RGB + depth to a working grid (max width `grid`, default 320) to bound payload,
  5. return `{ w, h, fov, image(b64 jpeg), depth(b64 float32, normalized 0..1 where 1=nearest),
     entities:[{id,cls,cx,cy,depth,label,conf}], cam, ts }`.
- `GET /api/spatial/{sid}?grid=` → `to_thread(spatial_scene)`; `{scene:null,reason}` when off.
- config `spatial:` — `enabled`, `model`, `input_width`, `grid`.

### Frontend
- `web/src/components/spatial/SpatialView.svelte` — a three.js overlay:
  - decode the RGB (canvas → colours) and the Float32 depth,
  - back-project each grid pixel: `Z = zmap(disp)`, `X=(u-cx)Z/f`, `Y=-(v-cy)Z/f`,
  - render as `THREE.Points` (vertex colours), OrbitControls to walk around,
  - overlay each detected entity as a billboard marker at its 3D position (id + class),
  - ground grid + fog for depth cues; refresh + auto-refresh; camera name; orbit hint.
- Placement: a **SPATIAL (3D)** control in the POV, a `spatialOpen` store rendered as an
  overlay for the active camera, a key, and a command-palette entry.

## Depth → Z mapping
Depth Anything outputs relative inverse-depth (disparity; larger = nearer). Normalize to
`disp01∈[0,1]` (1 = nearest). For a bounded, walk-around scene we map
`Z = znear + (1-disp01)^γ · (zfar-znear)` rather than true `1/disp` (which sends sky to
infinity). γ and znear/zfar are tuned during visual iteration.

## Verification
- **Pipeline (primary):** capture real frames from several configured cameras (street, airport,
  the looped feeds), run depth + back-projection, and render the point cloud from novel
  viewpoints offline (numpy perspective render). Coherent 3D layering across scenes = success.
- **Frontend:** `svelte-check` + `build`; render the component against a captured payload and
  screenshot it. Iterate γ / point size / colours to the best look.
- Backend unit tests for `spatial_scene` payload shape (mocked depth) + graceful-off path.

## Non-goals
Metric accuracy, multi-view fusion, persistent world model, mesh reconstruction. This is a
per-camera, on-demand spatial *view*, not a digital twin.
