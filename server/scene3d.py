"""Generative full-3D scene reconstruction from a single camera frame.

The flat depth mesh only knows the camera-facing surface, so orbiting reveals holes (behind
objects, off-frame). This pipeline COMPLETES the scene: it back-projects the frame into a point
cloud, then repeatedly renders the cloud from novel viewpoints, generatively inpaints the holes
that appear (a Stable-Diffusion inpainter hallucinates the occluded surroundings and object
sides), estimates depth for the new pixels, aligns that depth to the existing geometry and fuses
the new points in. After a few views the scene is watertight from the exploration cone — a
coherent 3D manifestation of the frame with the missing parts filled, not photorealistic but
hole-free. (Text2Room / 3D-Photo lineage.)

Heavy: needs CUDA + a diffusion inpainter (~3 GB) on top of Depth Anything. VRAM-gated; degrades
to None when unavailable so the caller can warn the operator.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import Any, Callable

import cv2
import numpy as np

log = logging.getLogger("overseer.scene3d")

MIN_VRAM_GB = 6.0   # below this the pipeline won't fit alongside the depth model — warn instead


def vram_gb() -> float:
    """Total CUDA VRAM in GB, or 0.0 if no CUDA."""
    try:
        import torch
        if not torch.cuda.is_available():
            return 0.0
        return float(torch.cuda.get_device_properties(0).total_memory) / 1e9
    except Exception:  # noqa: BLE001
        return 0.0


def _look_at(cam_pos: np.ndarray, target: np.ndarray) -> np.ndarray:
    """World->camera rotation (rows = camera x,y,z axes) for a camera at cam_pos looking at
    target, in OpenCV convention (camera x right, y down, z forward). The world here uses image
    axes too — +X right, +Y down, +Z into the scene — so view-0 (camera at origin) is identity."""
    fwd = target - cam_pos
    fwd = fwd / (np.linalg.norm(fwd) + 1e-9)          # +Z (into the scene)
    world_down = np.array([0.0, 1.0, 0.0])            # +Y is down
    right = np.cross(world_down, fwd); right /= (np.linalg.norm(right) + 1e-9)
    down = np.cross(fwd, right)
    return np.stack([right, down, fwd], axis=0)       # world->cam (R @ (Xw - cam_pos))


class Scene3D:
    def __init__(self, depth_estimator: Any, fov_deg: float = 60.0,
                 model: str = "stable-diffusion-v1-5/stable-diffusion-inpainting") -> None:
        self._depth = depth_estimator
        self._fov = float(fov_deg)
        self._model = model
        self._pipe: Any = None
        self._loaded = False
        self._failed = False
        self._lock = threading.Lock()

    # ---- lifecycle -------------------------------------------------------
    def available(self) -> bool:
        return not self._failed and vram_gb() >= MIN_VRAM_GB

    def _ensure(self) -> bool:
        if self._loaded:
            return True
        if self._failed:
            return False
        if vram_gb() < MIN_VRAM_GB:
            return False
        try:
            import torch
            from diffusers import AutoPipelineForInpainting
            pipe = AutoPipelineForInpainting.from_pretrained(
                self._model, torch_dtype=torch.float16, safety_checker=None)
            pipe = pipe.to("cuda")
            pipe.set_progress_bar_config(disable=True)
            self._pipe = pipe
            self._loaded = True
            log.info("scene3d inpainter ready: %s", self._model)
            return True
        except Exception:  # noqa: BLE001
            log.exception("scene3d inpainter load failed")
            self._failed = True
            return False

    # ---- geometry helpers ------------------------------------------------
    def _intrinsics(self, w: int, h: int) -> tuple[float, float, float]:
        fx = 0.5 * w / math.tan(math.radians(self._fov) / 2)
        return fx, w / 2.0, h / 2.0

    def _depth_z(self, bgr: np.ndarray) -> np.ndarray | None:
        """Monocular depth as a positive Z map (near small, far large), same HxW as bgr."""
        disp = self._depth.estimate(bgr)
        if disp is None:
            return None
        d = disp.astype(np.float32)
        d01 = (d - d.min()) / (d.max() - d.min() + 1e-6)      # 1 = nearest
        return (1.0 + (1.0 - d01) * 8.0).astype(np.float32)   # Z in [1, 9], monotone in inverse-depth

    def _splat(self, pts: np.ndarray, col: np.ndarray, R: np.ndarray, cam_pos: np.ndarray,
               w: int, h: int, fx: float, cx: float, cy: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Vectorised z-buffer splat of a world point cloud from a camera pose. Returns
        (rgb uint8 HxW3, filled bool HxW, zbuf float HxW)."""
        pc = (pts - cam_pos) @ R.T                 # world -> camera
        z = pc[:, 2]
        ok = z > 0.05
        u = (pc[:, 0] * fx / z + cx)
        v = (pc[:, 1] * fx / z + cy)
        ui = np.round(u).astype(np.int64); vi = np.round(v).astype(np.int64)
        inb = ok & (ui >= 0) & (ui < w) & (vi >= 0) & (vi < h)
        ui, vi, zz, cc = ui[inb], vi[inb], z[inb], col[inb]
        order = np.argsort(-zz)                      # far first so near overwrites (painter)
        ui, vi, zz, cc = ui[order], vi[order], zz[order], cc[order]
        rgb = np.zeros((h, w, 3), np.uint8)
        zbuf = np.full((h, w), np.inf, np.float32)
        filled = np.zeros((h, w), bool)
        # 2x2 splat to close sampling gaps (genuine holes stay empty)
        for dy in (0, 1):
            for dx in (0, 1):
                yy = np.clip(vi + dy, 0, h - 1); xx = np.clip(ui + dx, 0, w - 1)
                rgb[yy, xx] = cc; zbuf[yy, xx] = zz; filled[yy, xx] = True
        return rgb, filled, zbuf

    def _inpaint(self, rgb: np.ndarray, hole: np.ndarray) -> np.ndarray:
        from PIL import Image
        m = (hole.astype(np.uint8)) * 255
        m = cv2.dilate(m, np.ones((5, 5), np.uint8), 1)   # cover splat fringe
        res = self._pipe(
            prompt="a coherent continuous scene, natural surroundings, consistent surfaces, seamless",
            negative_prompt="blurry, distorted, text, watermark, frame, border",
            image=Image.fromarray(rgb), mask_image=Image.fromarray(m),
            num_inference_steps=18, guidance_scale=7.0,
            width=rgb.shape[1], height=rgb.shape[0]).images[0]
        return np.array(res)

    def reconstruct(self, frame_bgr: np.ndarray, size: int = 512, max_views: int = 99,
                    progress: Callable[[str, float], None] | None = None) -> dict | None:
        """Return {"points": Nx3 float32, "colors": Nx3 uint8, "fov": deg} for the completed
        scene, or None if unavailable."""
        with self._lock:
            if not self._ensure():
                return None
            rgb0 = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            h0, w0 = rgb0.shape[:2]
            W = size; H = int(round(size * h0 / w0 / 8) * 8)
            rgb0 = cv2.resize(rgb0, (W, H), interpolation=cv2.INTER_AREA)
            fx, cx, cy = self._intrinsics(W, H)
            if progress:
                progress("estimating depth", 0.1)
            z0 = self._depth_z(cv2.cvtColor(rgb0, cv2.COLOR_RGB2BGR))
            if z0 is None:
                return None
            # view-0 point cloud (world == camera-0)
            xs, ys = np.meshgrid(np.arange(W), np.arange(H))
            X = (xs - cx) * z0 / fx; Y = (ys - cy) * z0 / fx
            pts = np.stack([X, Y, z0], -1).reshape(-1, 3).astype(np.float32)
            col = rgb0.reshape(-1, 3).astype(np.uint8)
            centroid = pts.mean(0)
            dist = float(np.linalg.norm(centroid))
            # novel views: orbit around the centroid (view-0 is yaw=0)
            poses = [(-22, 5), (22, 5), (-12, -9), (12, -9), (0, 15), (0, -13),
                     (-34, 3), (34, 3), (-9, 20), (9, 20), (-30, -8), (30, -8),
                     # extra passes to reach concave/under-structure pockets the base
                     # orbit skims over: the display pose itself, wide sides, and
                     # steep dive/worm angles that look *into* shaded recesses
                     (16, 6), (-42, 9), (42, 9), (-22, -15), (22, -15),
                     (-15, 24), (0, -22)][:max_views]
            for k, (yaw_d, pitch_d) in enumerate(poses):
                if progress:
                    progress(f"completing view {k + 1}/{len(poses)}", 0.2 + 0.7 * k / len(poses))
                ya, pa = math.radians(yaw_d), math.radians(pitch_d)
                cam = centroid + dist * np.array([math.sin(ya) * math.cos(pa),
                                                  -math.sin(pa), -math.cos(ya) * math.cos(pa)])
                R = _look_at(cam, centroid)
                rgb, filled, zbuf = self._splat(pts, col, R, cam, W, H, fx, cx, cy)
                hole = ~filled
                # ignore tiny speckle holes; only inpaint real disocclusions
                hole = cv2.morphologyEx(hole.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)).astype(bool)
                frac = float(hole.mean())
                if frac < 0.02 or frac > 0.75:
                    continue
                filled_rgb = self._inpaint(rgb, hole)
                zc = self._depth_z(cv2.cvtColor(filled_rgb, cv2.COLOR_RGB2BGR))
                if zc is None:
                    continue
                # align the inpainted view's depth to the existing geometry on the KNOWN pixels
                known = filled & np.isfinite(zbuf)
                if known.sum() < 200:
                    continue
                a, b = np.polyfit(zc[known].ravel(), zbuf[known].ravel(), 1)
                z_aligned = a * zc + b
                z_aligned = np.clip(z_aligned, 0.3, 40.0)
                # back-project the NEW (inpainted) pixels into world and fuse
                nh = hole & (z_aligned > 0.3)
                uu, vv = xs[nh], ys[nh]
                zz = z_aligned[nh]
                Xc = (uu - cx) * zz / fx; Yc = (vv - cy) * zz / fx
                pc = np.stack([Xc, Yc, zz], -1).astype(np.float32)
                pw = pc @ R + cam                       # camera -> world
                pts = np.concatenate([pts, pw.astype(np.float32)], 0)
                col = np.concatenate([col, filled_rgb[nh].astype(np.uint8)], 0)
            if progress:
                progress("finalizing", 0.95)
            pts, col = self._voxel_downsample(pts, col, vox=dist * 0.004)
            return {"points": pts, "colors": col, "fov": self._fov}

    @staticmethod
    def _voxel_downsample(pts: np.ndarray, col: np.ndarray, vox: float,
                          cap: int = 240_000) -> tuple[np.ndarray, np.ndarray]:
        """Collapse points to one per voxel (keeps memory + payload bounded, removes overlap)."""
        if vox <= 0 or len(pts) == 0:
            return pts, col
        key = np.floor(pts / vox).astype(np.int64)
        _, idx = np.unique(key[:, 0] * 73856093 ^ key[:, 1] * 19349663 ^ key[:, 2] * 83492791,
                           return_index=True)
        pts, col = pts[idx], col[idx]
        if len(pts) > cap:
            sel = np.random.default_rng(0).choice(len(pts), cap, replace=False)
            pts, col = pts[sel], col[sel]
        return pts, col
