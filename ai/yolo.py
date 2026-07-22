"""Single-pass YOLO11 + ByteTrack backend shared by category detector plugins."""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np
from loguru import logger
from ultralytics import YOLO

from camera.frame_buffer import Frame
from core.config import Config
from plugins.base import BaseDetector, Detection

# Low-light enhancement: denoise luma → CLAHE → adaptive gamma. Denoising first
# stops night sensor-noise (and small light speckle) from becoming phantom
# detections, while the gamma lift reveals people/vehicles walking in the dark.
_CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def _gamma_lut(g: float) -> np.ndarray:
    return np.array([((i / 255.0) ** g) * 255 for i in range(256)], dtype=np.uint8)


_GAMMA_MILD = _gamma_lut(0.62)
_GAMMA_STRONG = _gamma_lut(0.5)


def _enhance_lowlight(img: np.ndarray, mean: float) -> np.ndarray:
    try:
        yuv = cv2.cvtColor(img, cv2.COLOR_BGR2YUV)
        y = cv2.medianBlur(yuv[:, :, 0], 3)  # remove speckle noise before boosting
        yuv[:, :, 0] = _CLAHE.apply(y)
        out = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR)
        return cv2.LUT(out, _GAMMA_STRONG if mean < 40 else _GAMMA_MILD)
    except Exception:  # noqa: BLE001
        return img

COCO_CLASS_MAP: dict[int, tuple[str, str]] = {
    0: ("person", "person"),
    1: ("vehicle", "bicycle"),
    2: ("vehicle", "car"),
    3: ("vehicle", "motorcycle"),
    5: ("vehicle", "bus"),
    7: ("vehicle", "truck"),
    14: ("animal", "bird"),
    15: ("animal", "cat"),
    16: ("animal", "dog"),
    17: ("animal", "horse"),
    18: ("animal", "sheep"),
    19: ("animal", "cow"),
    20: ("animal", "elephant"),
    21: ("animal", "bear"),
    22: ("animal", "zebra"),
    23: ("animal", "giraffe"),
    24: ("accessory", "backpack"),
    25: ("accessory", "umbrella"),
    26: ("accessory", "handbag"),
    28: ("accessory", "suitcase"),
    # weapon / danger (COCO-available; guns need a dedicated model)
    43: ("weapon", "knife"),
    76: ("weapon", "scissors"),
    34: ("weapon", "bat"),
}


class YoloBackend:
    def __init__(
        self,
        model_path: Path,
        device: str,
        confidence: float = 0.35,
        imgsz: int = 960,
        frame_interval: int = 2,
    ) -> None:
        self._model = YOLO(str(model_path))
        self._device = device
        self._confidence = confidence
        self._imgsz = imgsz
        self._frame_interval = max(1, frame_interval)
        self._lowlight = True  # auto-enhance dark frames before inference
        self._cache_seq = -1
        self._cache: list[Detection] = []
        self.inference_count = 0
        self.last_inference_ms = 0.0
        logger.info("YOLO backend ready on {} (model={})", device, model_path.name)

    def infer(self, frame: Frame) -> list[Detection]:
        if frame.seq == self._cache_seq:
            return self._cache
        if frame.seq % self._frame_interval != 0 and self._cache_seq >= 0:
            return self._cache
        started = time.perf_counter()
        src = frame.image
        mean = float(src.mean()) if getattr(src, "ndim", 0) == 3 else 255.0
        dark = self._lowlight and mean < 75.0
        if dark:
            src = _enhance_lowlight(src, mean)
        # In the dark, drop the confidence floor so faint pedestrians are still
        # found; generic objects are filtered downstream so this won't add noise.
        conf = self._confidence * 0.7 if dark else self._confidence
        results = self._model.track(
            source=src,
            persist=True,
            conf=conf,
            imgsz=self._imgsz,
            device=self._device,
            # ultralytics 8.4 deprecated the bool `half=` flag in favor of the unified
            # `quantize` scheme ("fp16"/"int8"/None); passing `half=` still works but
            # logs a deprecation warning on every call, so we target `quantize` directly.
            quantize="fp16" if self._device.startswith("cuda") else None,
            classes=list(COCO_CLASS_MAP.keys()),
            tracker="bytetrack.yaml",
            verbose=False,
        )
        self.last_inference_ms = (time.perf_counter() - started) * 1000.0
        self.inference_count += 1
        detections: list[Detection] = []
        boxes = results[0].boxes
        if boxes is not None:
            for i in range(len(boxes)):
                class_id = int(boxes.cls[i])
                mapped = COCO_CLASS_MAP.get(class_id)
                if mapped is None:
                    continue
                category, label = mapped
                x1, y1, x2, y2 = (int(v) for v in boxes.xyxy[i].tolist())
                track_id = int(boxes.id[i]) if boxes.id is not None else None
                detections.append(Detection(
                    label=label, confidence=float(boxes.conf[i]),
                    bbox=(x1, y1, x2, y2), category=category, track_id=track_id,
                ))
        self._cache_seq = frame.seq
        self._cache = detections
        return detections

    def detect_crop(self, img: np.ndarray, conf: float = 0.12) -> list[Detection]:
        """One-shot detection on an arbitrary (e.g. upscaled/enhanced) crop at a low
        confidence — used by 'look closer' to find what the live pass missed."""
        try:
            results = self._model(
                source=img, conf=conf, imgsz=max(self._imgsz, 1280), device=self._device,
                quantize="fp16" if self._device.startswith("cuda") else None,
                classes=list(COCO_CLASS_MAP.keys()), verbose=False,
            )
        except Exception:  # noqa: BLE001
            return []
        out: list[Detection] = []
        boxes = results[0].boxes
        if boxes is not None:
            for i in range(len(boxes)):
                mapped = COCO_CLASS_MAP.get(int(boxes.cls[i]))
                if mapped is None:
                    continue
                category, label = mapped
                x1, y1, x2, y2 = (int(v) for v in boxes.xyxy[i].tolist())
                out.append(Detection(label=label, confidence=float(boxes.conf[i]),
                                     bbox=(x1, y1, x2, y2), category=category, track_id=None))
        return out

    def close(self) -> None:
        self._cache = []


class YoloCategoryDetector(BaseDetector):
    def __init__(
        self,
        config: Config,
        backend: YoloBackend,
        name: str,
        display_name: str,
        category: str,
    ) -> None:
        self.name = name
        self.display_name = display_name
        super().__init__(config)
        self._backend = backend
        self._category = category

    def process(self, frame: Frame) -> list[Detection]:
        return [d for d in self._backend.infer(frame) if d.category == self._category]


def create_yolo_detectors(
    config: Config, backend: YoloBackend
) -> list[YoloCategoryDetector]:
    specs = [
        ("person", "Person Detection", "person"),
        ("vehicle", "Vehicle Detection", "vehicle"),
        ("animal", "Animal Detection", "animal"),
        ("accessory", "Accessory", "accessory"),
        ("weapon", "Weapon / Danger", "weapon"),
    ]
    return [
        YoloCategoryDetector(config, backend, name, display, category)
        for name, display, category in specs
    ]
