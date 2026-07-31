# Spatial 3D — real 3D objects in the scene

The SPATIAL (3D) view lifts a single camera frame into a navigable 3D scene. On top of the
back-projected depth surface, the classes we can detect reliably are placed as **real 3D
geometry** (not flat depth cut-outs), and the depth surface itself is cleaned so it reads as a
coherent scene instead of a stretched sheet.

This document covers the object-placement layer added on top of the base depth reconstruction
(the base pipeline is in `docs/superpowers/specs/2026-07-29-spatial-3d-scene-design.md`).

## What is real 3D vs. background

| Content | Representation | Why |
|---|---|---|
| **People** | Posed **SMPL** body mesh (ROMP), one per person | We have a reliable person detector + a monocular multi-person mesh regressor, so a real posed body is recoverable. |
| **Vehicles** | Low-poly **car mesh** (body + glass cabin + 4 wheels), tinted by the crop's mean colour | We detect vehicles; a class-typical car primitive placed at the right depth/size reads far better than the stretched depth cut-out. |
| **Trees, buildings, ground, clutter** | Textured **depth mesh** relief (grazing-cleaned) | No per-pixel semantic segmentation runs (kept out for performance — see *Non-goals*), so there is no reliable tree/building mask to fit a primitive to. They are already present in the background surface as textured 3D relief; forcing a guessed box/billboard would look worse, not better. |

The split is deliberate: place a clean 3D primitive **only** where detection is reliable enough
that the primitive is more faithful than the raw surface. Everywhere else, keep the real
textured geometry.

## People — posed SMPL bodies (ROMP)

- Backend `server/human_mesh.py` (`HumanMeshEstimator`) runs whole-frame ROMP and returns, per
  person: `verts` (6890×3, root-relative metres, quantised int16 mm), shared `faces` (uint16),
  and the normalized 2D centre + apparent size.
- `Backend.spatial_scene` ships `scene.people[] = {v, cx, cy, sw, sh}` and `scene.smpl_faces`.
- Frontend `buildPeople` (in `SpatialView.svelte`):
  - **ROMP's Y axis points down** — flip it (`y = -raw.y`) so bodies stand upright (this was the
    upside-down bug).
  - **Stand on the feet, not the centre:** sample the scene depth at the person's *foot* pixel
    (bottom-centre of the 2D box), searching outward for a valid/kept depth, and place the feet
    on the flattened ground (`Y≈0`). Placing at the body-centre depth made people float.
  - Scale so the body's world height matches a plausible human (clamped 1.2–2.1 m) at that depth.

## Vehicles — low-poly car mesh

- Backend: each detection entity now carries its normalized apparent size `sw`/`sh`
  (`_spatial_entities` in `server/backend.py`), so the frontend can size a primitive.
- Frontend `buildVehicles` (in `SpatialView.svelte`):
  - one shared unit car geometry (merged box body + box cabin + 4 cylinder wheels),
  - placed at the vehicle-body depth (`entity.depth`, **floored at 3 m** so a depth
    over-estimate can't blow one car up to fill the scene), on the ground,
  - width from apparent size, **clamped to a plausible car (1.5–3.2 m)**,
  - cabin verts darkened → reads as glass; body tinted by the crop's mean colour,
  - oriented side-on vs. facing-camera from the box aspect ratio,
  - overlapping detections de-duplicated (1.2 m NMS in world space).

## Background surface — grazing-angle cull

Raw monocular-depth meshes stretch into "drip" triangles at occlusion edges (depth smearing
across a near→far boundary), and outdoor scenes bunch the far ground into a spike. Tightening the
disparity-jump / Z-range culls did **not** remove these because the smeared depth has no sharp
jump to cut.

The fix is geometric: **cull triangles seen nearly edge-on**
(`|face_normal · view_dir| < graze`, default `0.2`). A drip that bridges a near pixel to a far
one is exactly such a grazing triangle; a surface that faces the camera (products, walls, people)
is kept. The threshold is gentle so a legitimately receding ground plane (only moderately
grazing) survives — only the near-90° smears go. Implemented in `layerMesh`
(`SpatialView.svelte`) and mirrored in the harness.

## Verification (with your own eyes)

Rendering runs real WebGL (GTAO + bloom + tone-map + sky), which the numpy harness can't do, so
we screenshot a headless Electron render and look at it:

```
cd web
node_modules/.bin/electron ../scripts/gl_capture.cjs --sid=<id> --mode=after --yaw=10 --pitch=8
# writes %TEMP%/rv_gl_after.png
```

Tuning passthroughs on the harness URL/`--extra=`: `render=splat`, `gz=<graze>`, `dj`, `zr`,
`ml`, `solid=0`. Good test sources: **Store Demo** (people), **CAR CAM** (vehicles), **Beach**
(flat outdoor — the hard case for monocular depth).

## Tests

- `tests/test_spatial_entities.py` — entities carry normalized `sw`/`sh`, centre, depth and
  completion boxes (fake detector, no full Backend).
- `scripts/test_spatial_geom.mjs` — `node scripts/test_spatial_geom.mjs`: the placement math
  (`zOf`, person foot-placement + height clamp, vehicle depth-floor + width-clamp + orientation,
  grazing cull) as pure-function invariants. Mirrors the inline math in `SpatialView.svelte`;
  keep in sync when that changes.
- `tests/test_spatial.py` — the existing payload/back-projection helpers.

## Non-goals / honest limits

- **No semantic segmentation** → no tree/building/road primitives; those stay as background
  relief. This is a performance choice, not an oversight.
- **Single frame, non-generative** → geometry occluded from the camera cannot be invented, so
  large oblique orbits reveal the 2.5-D nature of the background. The view is best near the
  original camera angle (parallax), with the placed people/vehicles giving the real 3D read.
- **Flat, textureless scenes** (open water/sand) have unreliable monocular depth and reconstruct
  poorly regardless of culling — an inherent limit of the input, not the placement layer.
- **Body pose is ROMP's estimate, shown faithfully.** On clear frames people stand/walk upright;
  on hard low-light frames the pose can be noisy. We do not force bodies upright — a person who is
  actually crouched or on the ground should read that way (that is exactly what an operator wants
  to notice), so faithful pose beats a prettier lie.
