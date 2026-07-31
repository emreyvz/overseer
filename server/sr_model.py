"""Learned super-resolution for photo reconstruction (feature 7).

Wraps Real-ESRGAN's lightweight general model (SRVGGNetCompact, ~5 MB) so a blurry face / plate / body
crop can be genuinely reconstructed, not just sharpened. The network architecture is vendored here
(a plain conv+PReLU stack, no basicsr / gfpgan / realesrgan package to fight the pinned env) and the
official weights auto-download to models/ on first use. Runs on CUDA when available.

Everything is best-effort: if torch is missing, the weights cannot be fetched, or anything throws,
`available()` is False and the caller falls back to the classical enhancement. Lazy singleton.
"""
from __future__ import annotations

import logging
import threading
import urllib.request
from pathlib import Path

import numpy as np

log = logging.getLogger("overseer.sr")

_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth"
_FILE = "realesr-general-x4v3.pth"
_SCALE = 4


def _build_net():
    import torch.nn as nn

    class SRVGGNetCompact(nn.Module):
        """Real-ESRGAN's compact generator: conv+PReLU body -> pixel-shuffle upsample + nearest skip."""

        def __init__(self, num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=32, upscale=4):
            super().__init__()
            self.upscale = upscale
            self.body = nn.ModuleList()
            self.body.append(nn.Conv2d(num_in_ch, num_feat, 3, 1, 1))
            self.body.append(nn.PReLU(num_parameters=num_feat))
            for _ in range(num_conv):
                self.body.append(nn.Conv2d(num_feat, num_feat, 3, 1, 1))
                self.body.append(nn.PReLU(num_parameters=num_feat))
            self.body.append(nn.Conv2d(num_feat, num_out_ch * upscale * upscale, 3, 1, 1))
            self.upsampler = nn.PixelShuffle(upscale)

        def forward(self, x):
            import torch.nn.functional as F
            out = x
            for layer in self.body:
                out = layer(out)
            out = self.upsampler(out)
            out += F.interpolate(x, scale_factor=self.upscale, mode="nearest")
            return out

    return SRVGGNetCompact()


class SuperResolver:
    scale = _SCALE

    def __init__(self, models_dir: str | Path = "models") -> None:
        self._dir = Path(models_dir)
        self._model = None
        self._device = None
        self._failed = False
        self._lock = threading.Lock()

    def _weights(self) -> Path | None:
        p = self._dir / _FILE
        if p.exists() and p.stat().st_size > 100_000:
            return p
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            log.info("downloading super-resolution weights (~5 MB, one time)...")
            urllib.request.urlretrieve(_URL, p)
            return p if p.exists() and p.stat().st_size > 100_000 else None
        except Exception as exc:  # noqa: BLE001
            log.warning("super-resolution weights unavailable: %s", exc)
            return None

    def _ensure(self) -> bool:
        if self._model is not None:
            return True
        if self._failed:
            return False
        with self._lock:
            if self._model is not None:
                return True
            if self._failed:
                return False
            try:
                import torch
                w = self._weights()
                if w is None:
                    self._failed = True
                    return False
                net = _build_net()
                sd = torch.load(str(w), map_location="cpu")
                net.load_state_dict(sd.get("params", sd), strict=True)
                self._device = "cuda" if torch.cuda.is_available() else "cpu"
                net.eval().to(self._device)
                self._model = net
                log.info("super-resolution ready (%s)", self._device)
                return True
            except Exception as exc:  # noqa: BLE001
                self._failed = True
                log.warning("super-resolution disabled: %s", exc)
                return False

    def available(self) -> bool:
        return self._ensure()

    def enhance(self, bgr: np.ndarray, *, max_side: int = 480) -> np.ndarray | None:
        """Super-resolve a BGR crop by the model's native scale (4x). The input is capped so a large
        crop cannot blow up GPU memory. Returns None if the model is unavailable or errors."""
        if bgr is None or getattr(bgr, "size", 0) == 0 or not self._ensure():
            return None
        try:
            import cv2
            import torch

            img = bgr
            h, w = img.shape[:2]
            if max(h, w) > max_side:
                s = max_side / max(h, w)
                img = cv2.resize(img, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
            ten = torch.from_numpy(rgb).permute(2, 0, 1).unsqueeze(0).to(self._device)
            with torch.no_grad():
                out = self._model(ten).clamp_(0, 1)
            out = (out.squeeze(0).permute(1, 2, 0).cpu().numpy() * 255.0).round().astype(np.uint8)
            return cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
        except Exception as exc:  # noqa: BLE001
            log.warning("super-resolution enhance failed: %s", exc)
            return None
