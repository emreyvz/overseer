"""Multi-view 3D scene reconstruction via DUSt3R (pure-PyTorch, no CUDA build).

A single surveillance frame can't be lifted to a real 3D scene — there is no parallax, so the
best a monocular guess can do is a curved "melted" depth sheet. But the cameras here move
(drone / vehicle / pan), so a short BURST of frames a fraction of a second apart carries genuine
parallax. DUSt3R (Naver) takes those uncalibrated views and regresses per-pixel 3D pointmaps plus
camera poses; a global alignment fuses them into one consistent point cloud. That is a real
multi-view-stereo reconstruction of the actual scene geometry — flat ground, true relative depth,
solid structures — rather than a hallucinated surface.

Heavy: a ViT-Large transformer (~2 GB weights, auto-downloaded from HuggingFace) plus a short
alignment optimisation. Runs in pure PyTorch on CUDA — the optional RoPE CUDA kernel is NOT
required (it falls back to a torch implementation). VRAM-gated; degrades to None when unavailable
so the caller can fall back or warn.

Setup: the DUSt3R repo is cloned (with its croco submodule) under third_party/dust3r; weights are
pulled on first use into the HuggingFace cache. See scripts/setup_dust3r.* / README.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from typing import Any, Callable

import cv2
import numpy as np

from . import scene3d as _s3   # reuse VRAM gate + density-filter / voxel-downsample helpers

log = logging.getLogger("overseer.mvsfm")

MIN_VRAM_GB = 6.0
_DUST3R_ROOT = os.path.join(os.path.dirname(os.path.dirname(__file__)), "third_party", "dust3r")
_MODEL = "naver/DUSt3R_ViTLarge_BaseDecoder_512_dpt"


def vram_gb() -> float:
    return _s3.vram_gb()


def dust3r_present() -> bool:
    """True if the DUSt3R clone is on disk (code + croco submodule)."""
    return os.path.isfile(os.path.join(_DUST3R_ROOT, "dust3r", "model.py")) and \
        os.path.isdir(os.path.join(_DUST3R_ROOT, "croco"))


def _add_paths() -> None:
    for p in (_DUST3R_ROOT, os.path.join(_DUST3R_ROOT, "croco")):
        if p not in sys.path:
            sys.path.insert(0, p)


class MultiViewScene:
    """Feed-forward multi-view stereo: a burst of frames -> a fused metric point cloud."""

    def __init__(self, model_name: str = _MODEL) -> None:
        self._name = model_name
        self._model: Any = None
        self._loaded = False
        self._failed = False
        self._lock = threading.Lock()

    # ---- lifecycle -------------------------------------------------------
    def available(self) -> bool:
        return not self._failed and dust3r_present() and vram_gb() >= MIN_VRAM_GB

    def _ensure(self) -> bool:
        if self._loaded:
            return True
        if self._failed or not dust3r_present() or vram_gb() < MIN_VRAM_GB:
            return False
        try:
            _add_paths()
            from dust3r.model import AsymmetricCroCo3DStereo
            self._model = AsymmetricCroCo3DStereo.from_pretrained(self._name).to("cuda")
            self._model.eval()
            self._loaded = True
            log.info("dust3r model ready: %s", self._name)
            return True
        except Exception:  # noqa: BLE001
            log.exception("dust3r load failed")
            self._failed = True
            return False

    # ---- input prep ------------------------------------------------------
    def _prep(self, bgr: np.ndarray, idx: int, size: int = 512, patch: int = 16) -> dict:
        """BGR frame -> DUSt3R input dict (mirrors dust3r.utils.image.load_images for arrays)."""
        import PIL.Image
        from dust3r.utils.image import ImgNorm, _resize_pil_image
        img = PIL.Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        img = _resize_pil_image(img, size)
        W, H = img.size
        cx, cy = W // 2, H // 2
        halfw = ((2 * cx) // patch) * patch / 2
        halfh = ((2 * cy) // patch) * patch / 2
        img = img.crop((cx - halfw, cy - halfh, cx + halfw, cy + halfh))
        return dict(img=ImgNorm(img)[None], true_shape=np.int32([img.size[::-1]]),
                    idx=idx, instance=str(idx))

    # ---- reconstruction --------------------------------------------------
    def reconstruct(self, frames: list[np.ndarray], size: int = 512, niter: int = 300,
                    conf_thr: float = 3.0,
                    progress: Callable[[str, float], None] | None = None) -> dict | None:
        """Fuse a burst of frames into {"points": Nx3 float32, "colors": Nx3 uint8, "fov"}.
        Returns None if unavailable or fewer than two usable views."""
        if len(frames) < 2:
            return None
        with self._lock:
            if not self._ensure():
                return None
            import torch
            from dust3r.image_pairs import make_pairs
            from dust3r.inference import inference
            from dust3r.cloud_opt import GlobalAlignerMode, global_aligner
            imgs = [self._prep(f, i, size) for i, f in enumerate(frames)]
            if progress:
                progress("matching viewpoints", 0.25)
            pairs = make_pairs(imgs, scene_graph="complete", symmetrize=True)
            with torch.no_grad():
                out = inference(pairs, self._model, "cuda", batch_size=1, verbose=False)
            if progress:
                progress("fusing geometry", 0.55)
            multi = len(imgs) > 2
            mode = GlobalAlignerMode.PointCloudOptimizer if multi else GlobalAlignerMode.PairViewer
            scene = global_aligner(out, device="cuda", mode=mode)
            if multi:
                scene.compute_global_alignment(init="mst", niter=niter, schedule="cosine", lr=0.01)
            scene.min_conf_thr = float(scene.conf_trf(torch.tensor(float(conf_thr))))
            pts3d = scene.get_pts3d()
            masks = scene.get_masks()
            ims = scene.imgs
            P: list[np.ndarray] = []
            C: list[np.ndarray] = []
            for im, pt, m in zip(ims, pts3d, masks):
                mm = m.detach().cpu().numpy()
                if mm.sum() == 0:
                    continue
                pp = pt.detach().cpu().numpy()
                P.append(pp[mm])
                C.append((np.asarray(im)[mm] * 255.0).astype(np.uint8))
            if not P:
                return None
            pts = np.concatenate(P).astype(np.float32)
            col = np.concatenate(C).astype(np.uint8)
            pts = self._orient(pts)
            # trim far outliers (sky / low-parallax background stretch far away and bloat the
            # scene bounds, shrinking the real geometry in view) — keep the compact 97% core
            r = np.linalg.norm(pts, axis=1)
            keep = r <= np.percentile(r, 97.0)
            pts, col = pts[keep], col[keep]
            if progress:
                progress("finalizing", 0.9)
            # DUSt3R is already clean, but a light density pass removes stray low-confidence specks
            span = float(np.linalg.norm(np.ptp(pts, axis=0))) or 1.0
            pts, col = _s3.Scene3D._density_filter(pts, col, vox=span * 0.02, min_neighbors=5)
            pts, col = _s3.Scene3D._voxel_downsample(pts, col, vox=span * 0.0035, cap=260_000)
            return {"points": pts, "colors": col, "fov": 60.0}

    @staticmethod
    def _orient(pts: np.ndarray) -> np.ndarray:
        """Recenter to the scene centroid and scale to a stable size for the renderer (fog / near-
        far / point-size heuristics assume a scene a few units across). DUSt3R's axes already match
        the app's image convention (x right, y down, z forward), so only translate + scale."""
        c = np.median(pts, axis=0)
        p = pts - c
        r = float(np.percentile(np.linalg.norm(p, axis=1), 90)) or 1.0
        return (p * (3.0 / r)).astype(np.float32)
