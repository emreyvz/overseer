# Overseer — Semantic World Model (3D SPATIAL rebuild)

> Status: **design / spec**. Replaces the old 3D SPATIAL modes (Street *diorama* and *points*
> reconstruction), which are removed. Clicking **⛶ 3D SPATIAL** will run this pipeline.
>
> Goal: from a single security-camera frame, produce a **clean, editable, semantically-correct
> procedural 3D world** — a hand-built-looking game level, **not** a photogrammetry scan. No NeRF,
> no Gaussian Splatting, no pixel reconstruction. Every visible thing becomes an **independent,
> editable 3D entity** with class, transform, dimensions, material and a generated/retrieved mesh,
> assembled into a **scene graph** exportable to glTF / Unity / Unreal / Godot / Blender.

License note: all "License" cells below are the maintainers' stated terms at time of writing and
**must be re-verified before any commercial use** — several (Hunyuan3D, SegFormer weights, some
depth nets, Objaverse assets) carry research-only or mixed per-asset terms.

---

## 1. Design principles

1. **Semantic, not reconstructive.** The image is a *reference* for *what/where/how-big*, never a
   source of geometry. Output = procedural/retrieved assets, not meshed pixels.
2. **Scene-Graph IR is the spine.** Perception writes a versioned intermediate representation (§4);
   realization reads it. This seam is what makes every stage independently swappable.
3. **Compose specialists, never one monolith.** 13 small models/algorithms, each replaceable
   behind a typed interface (§7).
4. **Realize by retrieve → procedural → generate**, in that cost order (§6). Only synthesize a
   bespoke mesh when neither a library asset nor a procedural generator fits.
5. **Infer the unseen.** Buildings extrude to full volumes, cars become whole vehicles, roads and
   sidewalks extend past the frame, terrain is completed. Occlusion is expected, not fatal.
6. **Budget-aware.** Perception + geometry run interactively on a 12 GB GPU; heavy mesh-generation
   is selective / async / retrieval-first (§8).

---

## 2. Pipeline overview (data flow)

```
                 ┌────────────────────────── PERCEPTION ──────────────────────────┐
  frame ─▶ (1) open-vocab detect ─▶ (2) segment (masks) ─▶ (5) semantic parse (stuff + VLM)
                 └───────────────┬───────────────────────────────────┬────────────┘
                                 ▼                                   ▼
             ┌──────────── GEOMETRY & CAMERA ───────────┐   ┌──── SCENE UNDERSTANDING ────┐
             │ (3) metric depth   (4) camera calib      │   │ (10) placement (pose/size)  │
             │     + focal/intrinsics + gravity/horizon │   │ (11) scene-graph relations  │
             └──────────────────────┬───────────────────┘   │ (12) physics/ground align   │
                                    ▼                        └──────────────┬──────────────┘
                          ╔═════════════════════════════════════════════════▼══════╗
                          ║         SCENE-GRAPH IR  (versioned JSON, §4)            ║
                          ╚═══════════════════════════════╤════════════════════════╝
                                                          ▼
             ┌──────────────────────── REALIZATION ───────────────────────────┐
             │ (6) terrain gen   (7) procedural assets   (8) mesh gen          │
             │ (9) texture / PBR materials   (13) lighting (HDRI + sun)        │
             └──────────────────────────────┬──────────────────────────────────┘
                                             ▼
                         glTF 2.0 scene  ─▶  three.js viewer / Unity / Unreal / Godot / Blender
```

Two composition strategies coexist (choose per deployment):

- **A — Compositional (recommended backbone).** Run stages 1→13 explicitly; maximal control,
  editability and swap-ability; matches "avoid one giant model."
- **B — Feedforward accelerator.** A single-image scene generator (**SceneGen**, 3DV 2026 — image +
  SAM2 masks → *N* posed 3D assets in one pass) can *bootstrap* stages 7-8-10 for the object layer,
  then results are normalized into the same IR and refined. Use B where latency matters and the
  scene is object-centric; keep A for terrain, roads, water, hidden-geometry and final editability.

---

## 3. Stage-by-stage model selection

Runtime/VRAM are approximate for **512–1024 px** inputs on the target **RTX 4070 (12 GB)** unless a
bigger GPU is noted. "Acc." is a rough quality expectation, not a single benchmark number.

### (1) Open-vocabulary object detection
| | |
|---|---|
| **Pick** | **Grounding DINO** (Swin-B) / **MM-Grounding-DINO** re-impl |
| Why | Text-promptable to *our* taxonomy (car, pole, bench, container, boat, barrier…) without retraining; referring expressions ("parked car", "person on bicycle"); strong zero-shot. |
| Acc. | ~52 AP COCO zero-shot; SOTA-class on ODinW/LVIS. |
| Runtime | ~0.1–0.3 s/frame | 
| GPU | ~4 GB |
| License | Apache-2.0 (IDEA / OpenMMLab) |
| Alternatives | **YOLO-World** (35 AP LVIS @ ~50 fps — use for real-time; note Ultralytics AGPL), **T-Rex2** (text+visual prompts, great for rare classes via example boxes), **OWLv2** (Apache), **RF-DETR** (closed-vocab but top COCO if taxonomy is fixed). |

### (2) Instance segmentation (mask per detection)
| | |
|---|---|
| **Pick** | **SAM 2.1** driven by the boxes from (1) → **Grounded-SAM-2** |
| Why | Pixel-accurate masks/alpha for cutouts, occlusion boundaries, and as SceneGen input; box-prompted so it inherits the open vocabulary. |
| Acc. | SOTA promptable segmentation; clean instance masks. |
| Runtime | ~0.05–0.2 s/object (batched) |
| GPU | ~3 GB |
| License | Apache-2.0 (Meta) |
| Alternatives | **SAM 2 (hiera-l)** for quality, **hiera-s/t** for speed; **OneFormer**/**Mask2Former** (panoptic in one shot). |

### (3) Monocular metric depth (+ focal if available)
| | |
|---|---|
| **Pick** | **Depth Pro** (Apple) — sharp metric depth **and focal-length** from one image, no intrinsics/metadata needed |
| Why | Single static camera has no parallax; we need *metric* depth + focal to lift pixels to metres. Depth Pro gives both in ~0.3 s. |
| Acc. | SOTA sharp boundaries; metric scale good outdoors within ~10–20%. |
| Runtime | ~0.3 s (2.25 MP) |
| GPU | ~4–6 GB |
| License | Apple ML research license — **verify for commercial** |
| Alternatives | **UniDepth V2** (metric depth **+ intrinsics** jointly; permissive-ish), **Metric3D v2** (metric depth + normals, needs intrinsics — pair with (4)), **MoGe-2** (metric point-maps, MIT), **Depth Anything V2** (relative — already in the repo; use as fallback + for normals). |

### (4) Camera calibration / pose (intrinsics, gravity, ground plane)
| | |
|---|---|
| **Pick** | **GeoCalib** (ECCV 2024) — focal, distortion, **gravity/horizon** via learned + geometric optimization |
| Why | The ground plane and "up" direction are what make objects *stand* and *sit* correctly; gravity feeds physics alignment (12). For a fixed cam, this is the "pose" (extrinsic to ground). |
| Acc. | SOTA single-image calibration (roll/pitch/focal). |
| Runtime | ~0.1 s |
| GPU | ~2 GB |
| License | Apache-2.0 (ETH CVG) |
| Alternatives | **PerspectiveFields** (up-vector + latitude fields → robust in the wild), **WildCamera**, or take intrinsics straight from Depth Pro/UniDepth and derive the ground plane by RANSAC-fitting the depth of ground-class pixels. |

### (5) Semantic scene understanding (stuff + scene type + attributes)
| | |
|---|---|
| **Pick** | **OneFormer** or **Mask2Former** (ADE20K/Cityscapes) for *stuff* (road, sidewalk, grass, water, sky, building) **+** a compact **VLM** (Qwen2.5-VL-7B / InternVL) for scene type, per-object attributes (colour, state: parked/moving), and relationship priors |
| Why | Detection handles *things*; segmentation of *stuff* defines terrain/road/water regions and the sky. The VLM adds the "understanding" layer: scene class (parking lot / beach / street), and structured attributes that seed the scene graph (11). |
| Acc. | ADE20K mIoU ~57–60 (OneFormer); VLM attributes qualitatively strong. |
| Runtime | seg ~0.2 s; VLM ~1–3 s |
| GPU | seg ~3 GB; VLM ~8–16 GB (quantize, or call the operator's configured AI provider) |
| License | OneFormer MIT; Mask2Former MIT/Apache; Qwen2.5-VL Apache-2.0; InternVL MIT |
| Alternatives | **SegFormer** (already used — light, but NVIDIA weights are research-only), **ODISE**, **SAN**. Route the VLM through the app's existing AI-provider config so it can be cloud or local. |

### (6) Terrain generation
| | |
|---|---|
| **Pick** | **Depth→heightfield + procedural extension** (own module), optionally **Infinigen** terrain nodes |
| Why | Fit a ground plane/heightfield to the depth of ground-class pixels (5), then **extend beyond the frame** with procedural noise matched at the seam; classify surface (asphalt/sand/grass/water) from (5) to pick the material. Clean editable mesh + heightmap, not a depth soup. |
| Acc. | Plausible, editable; not survey-accurate (by design). |
| Runtime | <0.1 s (heightfield) to seconds (Infinigen bake) |
| GPU | CPU-ok; Infinigen uses Blender |
| License | Infinigen **BSD-3-Clause** |
| Alternatives | **Gaea/World Machine** (not OSS), pure Perlin/Worley erosion, **Infinigen** full nature stack for rocks/cliffs/coastline. |

### (7) Procedural asset generation (terrain-scale + repeatable classes)
| | |
|---|---|
| **Pick** | **Infinigen** (trees, bushes, rocks, grass, nature) + **spline meshers** (roads/sidewalks/fences/lane-markings) + **procedural shaders** (water/pool/river) |
| Why | Infinite, parametric, editable, engine-friendly geometry with correct pivots — exactly "hand-built level" feel. Roads/sidewalks are spline-swept profiles that extend past the frame; water is a procedural surface + shader, never meshed pixels. |
| Acc. | Category-correct, stylised. |
| Runtime | ms (splines/shaders) to seconds (Infinigen tree bake, cache per class) |
| GPU | mostly CPU/Blender |
| License | Infinigen BSD-3; own modules MIT |
| Alternatives | **SceneCity** (road networks + city massing), **tree-gen**, **Sapling**, SpeedTree (not OSS). |

### (8) Per-object mesh generation (bespoke, only when needed)
| | |
|---|---|
| **Pick** | **TRELLIS** (Microsoft) for clean single-image→mesh; **Hunyuan3D 2.1** when **PBR materials** are wanted |
| Why | For unusual/unique objects with no good retrieval match (odd signage, custom structures), synthesize a full mesh from the cutout — this **infers unseen sides**. TRELLIS gives the cleanest topology; Hunyuan3D 2.1 outputs production PBR. |
| Acc. | Strong for compact objects; weaker for large scene-scale structures (use procedural for those). |
| Runtime | ~10–40 s/object |
| GPU | TRELLIS ~8–16 GB; Hunyuan3D ~10 GB+ (**tight on 12 GB — sequence, offload, or cap count**) |
| License | TRELLIS **MIT**; Hunyuan3D **Tencent community license (restrictions — verify)** |
| Alternatives | **InstantMesh** (Apache, faster/lower quality), **TripoSG**, **SceneGen** (whole-scene objects+positions in one A100 pass — strategy B). |

### (9) Texture / material synthesis
| | |
|---|---|
| **Pick** | **Hybrid**: project the source image onto asset UVs where the mapping is trustworthy (façades, ground); otherwise **generate PBR** (Hunyuan3D-2.1 PBR head, or a material library keyed by class) |
| Why | "Textures from the image only when useful." Projective texturing gives realism cheaply for flat, well-seen surfaces; class-based PBR (asphalt, brick, foliage, water, metal) keeps everything clean and tileable where projection would smear. |
| Acc. | Good for façades/ground; PBR library is clean and consistent. |
| Runtime | ms (projection / library) to ~5–15 s (generated PBR) |
| GPU | 0–8 GB |
| License | library = author's own; generators as in (8) |
| Alternatives | **MatSynth** dataset + retrieval, **StableMaterials**, **Dream Textures**, **text-to-PBR** diffusion. |

### (10) Object placement (world transform + size + orientation + elevation)
| | |
|---|---|
| **Pick** | **Geometric solver** (own): back-project each mask's ground-contact at its metric depth → world position; size from mask extent × depth ÷ focal; **orientation** from mask principal axis + VLM heading + (for vehicles) road-spline tangent; **elevation** from depth vs. local ground height |
| Why | Deterministic, explainable, editable. Uses (3)(4)(6) outputs. Places assets *on* the terrain with correct footprint and facing. |
| Acc. | Position good near ground contact; orientation heuristic (refine with strategy B / VLM). |
| Runtime | ms |
| GPU | none |
| License | own (MIT) |
| Alternatives | **SceneGen** relative positions (B), monocular 3D-object-detection nets (e.g. **CubeR-CNN / Omni3D**) for oriented 3D boxes directly. |

### (11) Scene-graph construction (relationships + hierarchy)
| | |
|---|---|
| **Pick** | **Geometric heuristics + VLM** → structured graph (SceneScript-style) |
| Why | Support/on-top/adjacent/part-of/parked-on/lane-of relations from 3D overlap + gravity, refined by the VLM's semantic reading ("car parked on road", "sign attached to pole"). Produces the parent/child hierarchy the IR needs. |
| Acc. | Reliable for geometric relations; semantic relations as good as the VLM. |
| Runtime | ms + one VLM call |
| GPU | see (5) |
| License | own MIT; VLM as (5) |
| Alternatives | **SceneCraft** (LLM→graph→Blender), **LayoutGPT**, **Holodeck** constraint language. |

### (12) Physics-aware alignment
| | |
|---|---|
| **Pick** | **Constraint solver** (own): snap to gravity-up, drop-to-ground (raycast onto terrain), resolve inter-object penetration, enforce support relations |
| Why | Makes the scene physically plausible — nothing floats or intersects; vehicles rest on the road, poles are vertical, boats sit in water. |
| Acc. | Deterministic; good with correct gravity (4) + terrain (6). |
| Runtime | ms |
| GPU | none |
| License | own MIT |
| Alternatives | **Holodeck**/**PhyScene** constraint optimisation, a real physics settle pass (PyBullet) for cluttered scenes. |

### (13) Lighting estimation
| | |
|---|---|
| **Pick** | **DiffusionLight** (chrome-ball inpainting → HDR probe) or **HDR-LDM** (latent-diffusion HDR env-map); plus **sun/sky fit** for outdoor |
| Why | One estimated **HDRI** + a directional **sun** (azimuth/elevation from sky/shadows) lights the whole level consistently and casts correct shadows — the difference between "game level" and "floating props." |
| Acc. | Plausible ambient + dominant light direction. |
| Runtime | DiffusionLight ~10–20 s (diffusion); analytic sky fit <1 s |
| GPU | ~4–8 GB (diffusion) |
| License | DiffusionLight (SD-derived, OpenRAIL — verify); analytic sky = own |
| Alternatives | **Deep Sky Model** (outdoor sun+sky HDR), **StyleLight**, or a cheap analytic sun from horizon (4) + sky colour (5). |

---

## 4. Scene-Graph IR (the contract)

Versioned JSON. Perception fills it; realization consumes it; export serializes it. Everything the
prompt asked for — id, class, confidence, transform, hierarchy, dims, material, mesh/texture refs,
metadata — lives here. **This schema is the stable API; models behind it can change freely.**

```jsonc
{
  "schema": "overseer.worldmodel/v1",
  "camera": { "intrinsics": {"fx","fy","cx","cy"}, "extrinsic": [/*4x4, world<-cam*/],
              "gravity": [x,y,z], "horizon": 0.0, "source_wh": [W,H] },
  "terrain": { "id","type":"asphalt|sand|grass|water|mixed",
               "heightfield_ref","material","bbox","extends_beyond_frame": true },
  "lighting": { "hdri_ref", "sun": {"dir":[x,y,z],"intensity","color"}, "ambient" },
  "nodes": [
    {
      "id": "obj_0007",
      "class": "car", "subtype": "sedan", "confidence": 0.87,
      "transform": { "position":[x,y,z], "rotation_quat":[x,y,z,w], "scale":[1,1,1] },
      "dimensions": { "w":1.8, "h":1.5, "l":4.6 },      // metres, incl. inferred hidden extent
      "elevation": 0.0,                                  // height of base above local ground
      "parent": "terrain_road_1", "children": [],
      "relations": [{"type":"parked_on","target":"terrain_road_1"}],
      "asset": { "strategy":"retrieve|procedural|generate",
                 "mesh_ref":"assets/vehicles/sedan_03.glb", "pivot":"base_center" },
      "material": { "type":"pbr", "material_ref":"mat/car_paint_blue",
                    "texture_ref":"proj/obj_0007.png|null" },
      "metadata": { "mask_area_px", "source_bbox", "detector":"gdino", "moving":false,
                    "occluded":true, "generator":null }
    }
  ]
}
```

Guarantees: stable `id`s, right-handed metric world (Y-up, gravity −Y), pivots defined per class
(vehicles/base-center, buildings/footprint-center, trees/trunk-base), and every node carries the
provenance to re-run just its stage.

---

## 5. Realization policy (retrieve → procedural → generate)

| Class group | Strategy | Source |
|---|---|---|
| vehicles, poles, signs, benches, bins, barriers, hydrants, boats, containers | **retrieve** | Objaverse-XL library, matched by class + CLIP embedding of the cutout; oriented by (10). Falls back to **generate** (TRELLIS) if no good match. |
| buildings, warehouses, walls, docks, bridges, stairs, fences | **procedural** | footprint (mask∩ground) extruded to inferred height; parametric façade; symmetric completion of unseen sides. |
| roads, sidewalks, lane-markings | **procedural (spline)** | centerline fit to region, swept profile, extended past frame. |
| terrain, rocks, mountains | **procedural** | heightfield + Infinigen. |
| trees, bushes, grass | **procedural (instanced)** | Infinigen/tree-gen; grass = instanced vegetation on grass-classed terrain. |
| water, pool, river, lake, sea | **procedural (shader)** | flat/So surface at water level + PBR water material. |
| unusual / unmatched objects | **generate** | TRELLIS / Hunyuan3D from the cutout (infers hidden sides). |

---

## 6. Inferring hidden geometry

- **Buildings**: footprint from the building mask's intersection with the ground plane; height from
  the mask's top back-projected; extrude to a closed volume; mirror/repeat façade for unseen sides.
- **Vehicles**: retrieval/generation returns a *whole* car; only pose+scale come from the image.
- **Roads/sidewalks**: spline extrapolated beyond the frame along its tangent until scene bounds.
- **Terrain**: ground heightfield extended with seam-matched procedural noise.
- **Trees**: full procedural tree from inferred canopy size; occluded parts are simply generated.

---

## 7. Modularity contract (swap any model without a redesign)

- Each stage is a Python **`Protocol`** with typed dataclass I/O, e.g.
  `Detector.detect(frame) -> list[Detection]`, `DepthModel.infer(frame) -> DepthResult`,
  `MeshRealizer.realize(node, ctx) -> MeshAsset`.
- A **registry + config** (`spatial.worldmodel.<stage>: <impl>`) selects the implementation; the
  orchestrator only ever touches the interfaces and the IR.
- The **IR is versioned**; stages declare which IR fields they read/write, so a replacement only has
  to honor that field contract. Per-stage **caching** keyed by (frame hash, impl, params).
- Result: replace Depth Pro → UniDepth, or TRELLIS → Hunyuan3D, or the geometric placer → SceneGen,
  by changing one config line and dropping in a class that satisfies the Protocol.

```
server/worldmodel/
  ir.py            # dataclasses + JSON (de)serialize, versioned
  registry.py      # stage interfaces (Protocols) + impl registry + config binding
  orchestrator.py  # runs enabled stages in order, reads/writes the IR, caches
  perception/      # detect.py segment.py parse.py
  geometry/        # depth.py calib.py placement.py
  graph/           # relations.py physics.py
  realize/         # terrain.py procedural.py meshgen.py materials.py lighting.py
  export/          # gltf.py  (+ engine notes)
```

---

## 8. Runtime & GPU budget (RTX 4070, 12 GB)

| Layer | Models | VRAM (sequential) | Time | Mode |
|---|---|---|---|---|
| Perception | GDINO + SAM2 + OneFormer | fits (~one at a time, 3–4 GB) | ~1–2 s | interactive |
| Geometry | Depth Pro + GeoCalib | ~4–6 GB | ~0.5 s | interactive |
| Understanding | VLM | 8–16 GB (quantized) or **cloud** | 1–3 s | interactive/async |
| Realize (retrieve+procedural) | Objaverse match + splines + shaders | small | ~1–3 s | interactive |
| Realize (generate) | TRELLIS/Hunyuan3D | 8–16 GB — **cap N, sequence, or async** | 10–40 s/obj | async/offline |
| Lighting | DiffusionLight | 4–8 GB | 10–20 s | async |

Strategy on 12 GB: **load models one stage at a time** (free between stages), prefer **retrieval +
procedural** for the interactive path, and push **per-object generation + lighting diffusion** to an
**async "enhance" pass** that upgrades assets in place after the first scene is already on screen.

---

## 9. Output & engine integration

- **glTF 2.0** as the interchange. Scene-graph → glTF node hierarchy; `KHR_materials_*` for PBR;
  `EXT_mesh_gpu_instancing` for grass/vegetation; `KHR_lights_punctual` + an HDRI sidecar for
  lighting; **`extras`** carries the full IR (id, class, confidence, relations, metadata) so nothing
  semantic is lost.
- Terrain = mesh + heightmap sidecar. Importers: **Unity** (glTFast), **Godot** (native glTF),
  **Blender** (native), **Unreal** (glTF/Datasmith). In-app viewer stays **three.js** (loads the
  same glTF), so the operator sees exactly what exports.

---

## 10. Phased build plan (each phase leaves 3D SPATIAL working)

- **Phase 1 — skeleton + IR (MVP).** GroundingDINO/YOLO-World + SAM2 + Depth Pro + GeoCalib →
  Scene-Graph IR → procedural ground plane + **retrieved/primitive** assets placed by the geometric
  solver → three.js viewer + glTF export. Fits the 4070; no heavy gen. *This is what 3D SPATIAL
  runs first.*
- **Phase 2 — environment.** Procedural terrain/roads/sidewalks/water + instanced trees/grass;
  building footprint-extrusion; hidden-geometry (vehicle retrieval, road extension).
- **Phase 3 — bespoke + look.** Per-object TRELLIS/Hunyuan3D for unmatched objects; projective +
  PBR materials; DiffusionLight HDRI + sun + shadows.
- **Phase 4 — understanding + engines.** VLM attributes & relations; physics alignment; full glTF
  export validated in Unity/Godot/Blender; optional SceneGen fast-path (strategy B).

---

## Sources
Detection: [Grounding DINO](https://github.com/IDEA-Research/GroundingDINO),
[YOLO-World](https://github.com/AILab-CVC/YOLO-World), [T-Rex2](https://github.com/IDEA-Research/T-Rex),
[RF-DETR / model roundup](https://blog.roboflow.com/best-object-detection-models/) ·
Segmentation: [SAM 2](https://github.com/facebookresearch/sam2),
[Grounded-SAM-2](https://github.com/IDEA-Research/Grounded-SAM-2) ·
Depth/geometry: [Depth Pro](https://github.com/apple/ml-depth-pro),
[UniDepth](https://github.com/lpiccinelli-eth/UniDepth),
[Metric3D v2](https://arxiv.org/html/2404.15506v4), [MoGe-2](https://arxiv.org/pdf/2507.02546) ·
Calibration: [GeoCalib](https://github.com/cvg/GeoCalib),
[PerspectiveFields](https://github.com/jinlinyi/PerspectiveFields) ·
Scene/asset gen: [SceneGen](https://github.com/Mengmouxu/SceneGen),
[TRELLIS](https://github.com/microsoft/TRELLIS),
[Hunyuan3D 2.1](https://arxiv.org/pdf/2506.15442), [InstantMesh](https://github.com/TencentARC/InstantMesh) ·
Procedural: [Infinigen](https://infinigen.org/), [SceneCraft](https://arxiv.org/abs/2403.01248),
[Holodeck](https://github.com/allenai/Holodeck) ·
Lighting: [DiffusionLight](https://diffusionlight.github.io/),
[HDR-LDM](https://arxiv.org/html/2507.21261v1).
