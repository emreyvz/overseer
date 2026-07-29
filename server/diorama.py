"""Semantic 3D diorama from a single frame.

The point-cloud reconstructions read as a fog of coloured dots — no object, no design. This takes
the opposite, "understand then build" approach: a scene-parsing model (SegFormer / ADE20K) labels
every pixel (sky, road, grass, water, building, tree, car, person, pole …) and monocular depth
places it. We then assemble a clean, readable 3D DIORAMA:

  * ground classes (road, grass, water, sidewalk …) become a flat textured ground surface,
  * the sky becomes the background colour,
  * every "thing" (tree, car, person, building, pole …) is cut out of the frame and stood UP as an
    image-textured billboard at its real 3D position — trees/people as cross-billboards so they
    read as solid from any orbit angle.

The result is a stylised-but-real 3D scene (textures come straight from the photo), not a sphere
soup. Heavy-ish: a small segmentation transformer (~100 MB) + monocular depth. VRAM-gated.
"""
from __future__ import annotations

import base64
import io
import logging
import math
import threading
from typing import Any, Callable

import cv2
import numpy as np

log = logging.getLogger("overseer.diorama")

MIN_VRAM_GB = 3.0
_SEG_MODEL = "nvidia/segformer-b2-finetuned-ade-512-512"

# ADE20K class-name -> role. Anything not listed is treated as a generic upright billboard.
_GROUND = {
    "road", "sidewalk", "floor", "earth", "grass", "path", "runway", "field", "land", "sand",
    "water", "sea", "river", "lake", "swimming pool", "pool", "dirt track", "hill", "pier",
    "playingfield", "platform", "beach",
}
_SKY = {"sky"}
_CROSS = {"tree", "plant", "palm", "person", "flower", "pole", "streetlight", "traffic light"}
_TALL = {"building", "house", "skyscraper", "wall", "tower", "hovel", "grandstand", "bridge",
         "mountain", "rock", "fence", "column", "chimney"}
# everything else vertical (car, truck, bus, bench, signboard, …) -> a plain upright billboard


def vram_gb() -> float:
    try:
        import torch
        if not torch.cuda.is_available():
            return 0.0
        return float(torch.cuda.get_device_properties(0).total_memory) / 1e9
    except Exception:  # noqa: BLE001
        return 0.0


def _b64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr).tobytes()).decode("ascii")


def _png_rgba(rgba: np.ndarray) -> str:
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else ""


class DioramaScene:
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
        import time as _time
        # transformers 5.x's lazy top-level imports are not fully thread-safe; this runs in a worker
        # thread that can race the analysis thread's transformers use and get a spurious ImportError.
        # Retry those (don't cache as a hard failure) and fall back to the concrete processor class.
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
                log.info("diorama segmenter ready: %s", self._model_name)
                return True
            except ImportError as exc:
                log.warning("diorama transformers import race (attempt %d): %s", attempt + 1, exc)
                _time.sleep(0.5)
            except Exception:  # noqa: BLE001
                log.exception("diorama segmenter load failed")
                self._failed = True
                return False
        return False   # transient import kept failing; leave un-cached so a later call retries

    # ---- helpers ---------------------------------------------------------
    def _segment(self, rgb: np.ndarray) -> np.ndarray:
        import torch
        h, w = rgb.shape[:2]
        inp = self._proc(images=rgb, return_tensors="pt").to("cuda")
        with torch.no_grad():
            logits = self._seg(**inp).logits
        up = torch.nn.functional.interpolate(logits, size=(h, w), mode="bilinear",
                                             align_corners=False)
        return up.argmax(1)[0].cpu().numpy().astype(np.int32)

    def _depth_disp(self, bgr: np.ndarray) -> np.ndarray | None:
        disp = self._depth.estimate(bgr)
        if disp is None:
            return None
        d = disp.astype(np.float32)
        return (d - d.min()) / (d.max() - d.min() + 1e-6)   # 1 = nearest

    # matches the frontend's zOf (ZNEAR=1, ZFAR=9, GAMMA=1.6) so objects and the ground mesh — which
    # the browser back-projects with that curve — sit at the same depths.
    @staticmethod
    def _z_of(disp01: np.ndarray) -> np.ndarray:
        return (1.0 + np.power(1.0 - disp01, 1.6) * 8.0).astype(np.float32)

    def _role(self, name: str) -> str:
        if name in _CROSS:
            return "cross"
        if name in _TALL:
            return "tall"
        return "flat"

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
                progress("parsing scene", 0.3)
            seg = self._segment(rgb)
            if progress:
                progress("estimating depth", 0.55)
            disp = self._depth_disp(bgr)
            if disp is None:
                return None
            Z = self._z_of(disp).astype(np.float32)
            fx = 0.5 * W / math.tan(math.radians(self._fov) / 2)
            cx, cy = W / 2.0, H / 2.0

            names = {i: self._id2label.get(i, str(i)) for i in np.unique(seg)}
            ground_mask = np.zeros((H, W), bool)
            sky_mask = np.zeros((H, W), bool)
            for i, nm in names.items():
                if nm in _GROUND:
                    ground_mask |= (seg == i)
                elif nm in _SKY:
                    sky_mask |= (seg == i)
            vertical_mask = ~(ground_mask | sky_mask)

            # ---- ground layer: disparity kept only on ground pixels (frontend meshes it) ----
            ground_disp = np.where(ground_mask, disp, 0.0).astype(np.float32)
            # a faint ground also under the vertical objects so their bases aren't floating: keep a
            # thin apron of non-sky pixels near the bottom of each vertical region (handled by the
            # objects themselves standing on the ground; ground holes read fine from above).

            if progress:
                progress("building objects", 0.75)
            objects = self._objects(seg, names, rgb, Z, disp, vertical_mask, fx, cx, cy, W, H)

            sky_rgb = (rgb[sky_mask].mean(0) if sky_mask.any() else np.array([90, 120, 160]))
            ok, jpg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if progress:
                progress("finalizing", 0.95)
            return {
                "mode": "diorama", "fov": self._fov, "w": W, "h": H,
                "ground_image": base64.b64encode(jpg.tobytes()).decode("ascii"),
                "ground_disp": _b64(ground_disp),
                "objects": objects,
                "sky": [int(sky_rgb[0]), int(sky_rgb[1]), int(sky_rgb[2])],
            }

    def _objects(self, seg: np.ndarray, names: dict[int, str], rgb: np.ndarray, Z: np.ndarray,
                 disp: np.ndarray, vertical: np.ndarray, fx: float, cx: float, cy: float,
                 W: int, H: int) -> list[dict]:
        """Connected-component instances of every vertical class -> upright textured cutouts."""
        min_area = int(0.0012 * W * H)          # drop specks
        objs: list[dict] = []
        for cid, nm in names.items():
            if nm in _GROUND or nm in _SKY:
                continue
            mask = ((seg == cid) & vertical).astype(np.uint8)
            if mask.sum() < min_area:
                continue
            n, lbl, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
            role = self._role(nm)
            for k in range(1, n):
                area = stats[k, cv2.CC_STAT_AREA]
                if area < min_area:
                    continue
                x, y, bw, bh = (stats[k, cv2.CC_STAT_LEFT], stats[k, cv2.CC_STAT_TOP],
                                stats[k, cv2.CC_STAT_WIDTH], stats[k, cv2.CC_STAT_HEIGHT])
                comp = (lbl[y:y + bh, x:x + bw] == k)
                # ground-contact depth: median Z over the bottom eighth of the component
                ys, xs = np.where(comp)
                base_v = y + ys.max()
                base_u = x + int(xs.mean())
                low = ys >= (ys.max() - max(1, bh // 8))
                zb = float(np.median(Z[y + ys[low], x + xs[low]]))
                if not np.isfinite(zb) or zb <= 0:
                    zb = float(np.median(Z[y:y + bh, x:x + bw]))
                # world position of the base (matches the ground-mesh convention: x right, y up,
                # z = -depth into the scene)
                X = (base_u - cx) * zb / fx
                Ybase = -(base_v - cy) * zb / fx
                Zc = -zb
                w_world = bw * zb / fx
                h_world = bh * zb / fx
                # textured cutout (RGBA, alpha = component mask)
                crop = rgb[y:y + bh, x:x + bw]
                mean_b = float(crop[comp].mean()) if comp.any() else 0.0
                if mean_b < 34.0:                        # near-black shadowed clutter reads as junk
                    continue
                if w_world < 0.28 and h_world < 0.28:    # slivers / specks at world scale
                    continue
                alpha = (comp.astype(np.uint8) * 255)
                # feather 1px so edges aren't hard
                alpha = cv2.erode(alpha, np.ones((2, 2), np.uint8), 1)
                rgba = np.dstack([crop, alpha]).astype(np.uint8)
                objs.append({
                    "cls": nm, "role": role,
                    "pos": [round(X, 4), round(Ybase, 4), round(Zc, 4)],
                    "w": round(w_world, 4), "h": round(h_world, 4),
                    "tex": _png_rgba(rgba),
                    "area": int(area),
                })
        # biggest first, cap payload
        objs.sort(key=lambda o: -o["area"])
        return objs[:64]
