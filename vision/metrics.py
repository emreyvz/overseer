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
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    denoised = cv2.medianBlur(gray, 3)
    noise = float(np.abs(gray.astype(np.int16) - denoised.astype(np.int16)).mean())
    return ImageMetrics(
        brightness=brightness, contrast=contrast, sharpness=sharpness, noise=noise
    )
