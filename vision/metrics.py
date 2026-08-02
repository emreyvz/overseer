"""Per-frame image quality metrics shown on the dashboard."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class ImageMetrics:
    brightness: float
    contrast: float
    sharpness: float
    noise: float


def compute_metrics(image: np.ndarray) -> ImageMetrics:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    brightness = float(gray.mean())
    contrast = float(gray.std())
    # The Laplacian-variance sharpness + median-blur noise are the costly parts (~30 ms on 1080p and
    # this runs every frame). Compute them on a downscaled gray (32F Laplacian) for the same relative
    # read at a fraction of the cost; brightness/contrast stay on the full frame (a cheap mean/std).
    h, w = gray.shape[:2]
    g = cv2.resize(gray, (640, max(1, round(h * 640 / w)))) if w > 640 else gray
    sharpness = float(cv2.Laplacian(g, cv2.CV_32F).var())
    denoised = cv2.medianBlur(g, 3)
    noise = float(np.abs(g.astype(np.int16) - denoised.astype(np.int16)).mean())
    return ImageMetrics(
        brightness=brightness, contrast=contrast, sharpness=sharpness, noise=noise
    )
