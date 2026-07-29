"""Semantic world model — a camera frame -> a clean, editable procedural 3D scene graph.

This is NOT reconstruction (no NeRF / splatting / pixel meshing). The frame is a *reference* for
WHAT is in the scene, WHERE it sits, and HOW BIG it is. We parse the scene, place every thing on an
inferred ground, and emit a versioned Scene-Graph IR (see docs/world-model-architecture.md) whose
nodes are independent, editable entities — each with class, confidence, world transform, metric-ish
dimensions, material hint and an asset-realization strategy. Realization (procedural buildings/
trees/roads, retrieved vehicles, generated meshes, PBR materials, lighting) reads this IR and can be
swapped out per stage without touching perception.

Phase 1 (this module): scene parsing (SegFormer/ADE20K) + monocular depth (Depth Anything, already
loaded) + a pinhole ground solver -> IR nodes. The browser renders nodes as clean placeholder
volumes; Phase 2+ swaps those for real procedural / retrieved / generated assets behind the same IR.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Callable

import cv2
import numpy as np

log = logging.getLogger("overseer.worldmodel")

MIN_VRAM_GB = 3.0
_SEG_MODEL = "nvidia/segformer-b2-finetuned-ade-512-512"
SCHEMA = "overseer.worldmodel/v1"

# ADE20K class-name -> semantic role in the world model.
_GROUND = {"road", "sidewalk", "floor", "earth", "grass", "path", "runway", "field", "land",
           "sand", "water", "sea", "river", "lake", "swimming pool", "pool", "dirt track", "hill",
           "pier", "playingfield", "platform", "beach"}
_SKY = {"sky"}
# realization strategy + a base tint (RGB) used for the Phase-1 placeholder volumes.
_ROLE: dict[str, tuple[str, str, tuple[int, int, int]]] = {
    # class-name: (semantic role, asset strategy, tint)
    "building": ("building", "procedural", (176, 168, 150)),
    "house": ("building", "procedural", (180, 170, 150)),
    "skyscraper": ("building", "procedural", (170, 175, 185)),
    "wall": ("wall", "procedural", (170, 165, 155)),
    "fence": ("fence", "procedural", (150, 130, 100)),
    "bridge": ("bridge", "procedural", (150, 150, 155)),
    "tree": ("tree", "procedural", (70, 120, 60)),
    "palm": ("tree", "procedural", (80, 130, 70)),
    "plant": ("bush", "procedural", (90, 130, 70)),
    "flower": ("bush", "procedural", (120, 130, 80)),
    "car": ("vehicle", "retrieve", (120, 130, 150)),
    "truck": ("vehicle", "retrieve", (120, 120, 130)),
    "bus": ("vehicle", "retrieve", (150, 140, 90)),
    "van": ("vehicle", "retrieve", (130, 135, 145)),
    "boat": ("boat", "retrieve", (140, 140, 150)),
    "minibike": ("motorcycle", "retrieve", (140, 120, 120)),
    "bicycle": ("bicycle", "retrieve", (140, 130, 120)),
    "person": ("person", "retrieve", (200, 170, 150)),
    "pole": ("pole", "retrieve", (120, 120, 120)),
    "streetlight": ("streetlight", "retrieve", (120, 120, 120)),
    "traffic light": ("traffic_light", "retrieve", (120, 120, 120)),
    "signboard": ("sign", "retrieve", (150, 150, 120)),
    "bench": ("bench", "retrieve", (140, 110, 80)),
    "awning": ("awning", "procedural", (160, 140, 120)),
    "mountain": ("mountain", "procedural", (120, 120, 110)),
    "rock": ("rock", "procedural", (130, 125, 115)),
}
# depth-extent (length along view) as a fraction of the object's measured width, per role — a rough
# hidden-geometry prior so placeholder volumes aren't paper-thin (Phase 2 replaces with real assets).
_DEPTH_FACTOR = {"building": 0.7, "wall": 0.15, "fence": 0.1, "bridge": 0.6, "vehicle": 1.6,
                 "boat": 1.8, "person": 0.5, "tree": 1.0, "bush": 1.0, "pole": 1.0,
                 "streetlight": 1.0, "traffic_light": 1.0, "sign": 0.15, "bench": 0.8,
                 "motorcycle": 1.8, "bicycle": 1.6, "mountain": 1.0, "rock": 1.0, "awning": 0.6}


def vram_gb() -> float:
    try:
        import torch
        if not torch.cuda.is_available():
            return 0.0
        return float(torch.cuda.get_device_properties(0).total_memory) / 1e9
    except Exception:  # noqa: BLE001
        return 0.0


class WorldModel:
    """Phase-1 world-model builder: parse + depth -> Scene-Graph IR."""

    def __init__(self, depth_estimator: Any, detector: Any = None,
                 fov_deg: float = 60.0, model: str = _SEG_MODEL) -> None:
        self._depth = depth_estimator
        self._det = detector
        self._fov = float(fov_deg)
        self._model_name = model
        self._proc: Any = None
        self._seg: Any = None
        self._id2label: dict[int, str] = {}
        self._loaded = False
        self._failed = False
        self._lock = threading.Lock()

    # ---- lifecycle -------------------------------------------------------
    def available(self) -> bool:
        return not self._failed and vram_gb() >= MIN_VRAM_GB

    def _ensure(self) -> bool:
        if self._loaded:
            return True
        if self._failed or vram_gb() < MIN_VRAM_GB:
            return False
        # transformers 5.x lazy top-level imports aren't thread-safe; retry the (worker-thread) race.
        for attempt in range(5):
            try:
                from transformers import SegformerForSemanticSegmentation
                try:
                    from transformers import AutoImageProcessor as _Proc
                except ImportError:
                    from transformers import SegformerImageProcessor as _Proc
                self._proc = _Proc.from_pretrained(self._model_name)
                self._seg = SegformerForSemanticSegmentation.from_pretrained(
                    self._model_name).to("cuda").eval()
                self._id2label = {int(k): v for k, v in self._seg.config.id2label.items()}
                self._loaded = True
                log.info("worldmodel segmenter ready: %s", self._model_name)
                return True
            except ImportError as exc:
                log.warning("worldmodel transformers import race (attempt %d): %s", attempt + 1, exc)
                time.sleep(0.5)
            except Exception:  # noqa: BLE001
                log.exception("worldmodel segmenter load failed")
                self._failed = True
                return False
        return False

    # ---- perception helpers ---------------------------------------------
    def _segment(self, rgb: np.ndarray) -> np.ndarray:
        import torch
        h, w = rgb.shape[:2]
        inp = self._proc(images=rgb, return_tensors="pt").to("cuda")
        with torch.no_grad():
            logits = self._seg(**inp).logits
        up = torch.nn.functional.interpolate(logits, size=(h, w), mode="bilinear",
                                             align_corners=False)
        return up.argmax(1)[0].cpu().numpy().astype(np.int32)

    def _depth_z(self, bgr: np.ndarray) -> np.ndarray | None:
        disp = self._depth.estimate(bgr)
        if disp is None:
            return None
        d = disp.astype(np.float32)
        d01 = (d - d.min()) / (d.max() - d.min() + 1e-6)      # 1 = nearest
        return (1.0 + np.power(1.0 - d01, 1.6) * 8.0).astype(np.float32)   # Z in [1, 9]

    # ---- build -----------------------------------------------------------
    def build(self, frame_bgr: np.ndarray, size: int = 640,
              progress: Callable[[str, float], None] | None = None) -> dict | None:
        with self._lock:
            if not self._ensure():
                return None
            h0, w0 = frame_bgr.shape[:2]
            W = size
            H = int(round(size * h0 / w0 / 8) * 8)
            bgr = cv2.resize(frame_bgr, (W, H), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            if progress:
                progress("parsing scene", 0.35)
            seg = self._segment(rgb)
            if progress:
                progress("estimating depth", 0.6)
            Z = self._depth_z(bgr)
            if Z is None:
                return None
            fx = 0.5 * W / math.tan(math.radians(self._fov) / 2)
            cx, cy = W / 2.0, H / 2.0

            names = {int(i): self._id2label.get(int(i), str(i)) for i in np.unique(seg)}
            sky_mask = np.zeros((H, W), bool)
            for i, nm in names.items():
                if nm in _SKY:
                    sky_mask |= (seg == i)

            if progress:
                progress("placing objects", 0.8)
            nodes = self._nodes(seg, names, rgb, Z, fx, cx, cy, W, H)
            # recenter the scene on the ground so it sits at the origin, Y up
            ground_y = 0.0
            if nodes:
                bases = np.array([n["_baseY"] for n in nodes], np.float32)
                cxs = np.array([n["transform"]["position"][0] for n in nodes], np.float32)
                czs = np.array([n["transform"]["position"][2] for n in nodes], np.float32)
                ground_y = float(np.percentile(bases, 12))
                mx, mz = float(np.median(cxs)), float(np.median(czs))
                for n in nodes:
                    p = n["transform"]["position"]
                    p[0] -= mx
                    p[1] -= ground_y
                    p[2] -= mz
                    n["elevation"] = round(max(0.0, n.pop("_baseY") - ground_y), 3)
                span = float(max(np.ptp(cxs), np.ptp(czs), 4.0))
            else:
                span = 12.0

            sky_rgb = (rgb[sky_mask].mean(0) if sky_mask.any() else np.array([135, 165, 200]))
            terrain_type = self._dominant_ground(seg, names)
            if progress:
                progress("finalizing", 0.95)
            return {
                "schema": SCHEMA,
                "mode": "worldmodel",
                "camera": {"fov": self._fov, "w": W, "h": H,
                           "intrinsics": {"fx": fx, "fy": fx, "cx": cx, "cy": cy}},
                "terrain": {"id": "terrain_0", "type": terrain_type,
                            "size": round(span * 1.6, 2), "material": terrain_type},
                "lighting": {"sky": [int(sky_rgb[0]), int(sky_rgb[1]), int(sky_rgb[2])],
                             "sun": [0.4, 0.9, 0.3]},
                "nodes": nodes,
            }

    def _dominant_ground(self, seg: np.ndarray, names: dict[int, str]) -> str:
        best, area = "asphalt", 0
        m = {"road": "asphalt", "sidewalk": "concrete", "grass": "grass", "sand": "sand",
             "earth": "dirt", "water": "water", "sea": "water", "river": "water", "lake": "water",
             "swimming pool": "water", "pool": "water", "beach": "sand", "field": "grass",
             "floor": "concrete", "path": "concrete"}
        for i, nm in names.items():
            if nm in _GROUND:
                a = int((seg == i).sum())
                if a > area:
                    area, best = a, m.get(nm, "asphalt")
        return best

    def _nodes(self, seg: np.ndarray, names: dict[int, str], rgb: np.ndarray, Z: np.ndarray,
               fx: float, cx: float, cy: float, W: int, H: int) -> list[dict]:
        min_area = int(0.0016 * W * H)
        nodes: list[dict] = []
        idx = 0
        for cid, nm in names.items():
            if nm in _GROUND or nm in _SKY or nm not in _ROLE:
                continue
            role, strategy, tint = _ROLE[nm]
            mask = (seg == cid).astype(np.uint8)
            if mask.sum() < min_area:
                continue
            n_cc, lbl, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
            for k in range(1, n_cc):
                area = int(stats[k, cv2.CC_STAT_AREA])
                if area < min_area:
                    continue
                x, y = int(stats[k, cv2.CC_STAT_LEFT]), int(stats[k, cv2.CC_STAT_TOP])
                bw, bh = int(stats[k, cv2.CC_STAT_WIDTH]), int(stats[k, cv2.CC_STAT_HEIGHT])
                comp = lbl[y:y + bh, x:x + bw] == k
                ys, xs = np.where(comp)
                if comp.mean() and float(rgb[y:y + bh, x:x + bw][comp].mean()) < 30:
                    continue
                base_v = y + int(ys.max())
                base_u = x + int(xs.mean())
                low = ys >= (ys.max() - max(1, bh // 8))
                zb = float(np.median(Z[y + ys[low], x + xs[low]]))
                if not np.isfinite(zb) or zb <= 0:
                    zb = float(np.median(Z[y:y + bh, x:x + bw]))
                Xc = (base_u - cx) * zb / fx
                Ybase = -((base_v - cy) * zb / fx)
                Zc = -zb
                w_world = max(bw * zb / fx, 0.05)
                h_world = max(bh * zb / fx, 0.05)
                l_world = max(w_world * _DEPTH_FACTOR.get(role, 0.5), 0.05)
                if w_world < 0.25 and h_world < 0.25:
                    continue
                idx += 1
                nodes.append({
                    "id": f"obj_{idx:04d}",
                    "class": role, "subtype": nm, "confidence": round(min(0.99, area / (W * H) * 6 + 0.4), 2),
                    "transform": {"position": [round(Xc, 3), round(Ybase, 3), round(Zc, 3)],
                                  "rotation_quat": [0.0, 0.0, 0.0, 1.0],
                                  "scale": [1.0, 1.0, 1.0]},
                    "dimensions": {"w": round(w_world, 3), "h": round(h_world, 3), "l": round(l_world, 3)},
                    "elevation": 0.0,
                    "parent": "terrain_0", "children": [], "relations": [],
                    "asset": {"strategy": strategy, "mesh_ref": None, "pivot": "base_center"},
                    "material": {"type": "pbr", "tint": list(tint), "texture_ref": None},
                    "metadata": {"mask_area_px": area, "source_bbox": [x, y, bw, bh],
                                 "parser": "segformer_ade20k", "occluded": bool(comp.mean() < 0.6)},
                    "_baseY": Ybase,
                })
        nodes.sort(key=lambda n: -n["metadata"]["mask_area_px"])
        return nodes[:80]
