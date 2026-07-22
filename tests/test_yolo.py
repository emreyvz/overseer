from pathlib import Path

import cv2
import pytest
from ultralytics.utils import ASSETS

from ai.model_manager import ModelManager
from ai.yolo import COCO_CLASS_MAP, YoloBackend, create_yolo_detectors
from camera.frame_buffer import Frame
from core.config import Config, load_config


@pytest.fixture(scope="module")
def backend() -> YoloBackend:
    model_path = ModelManager(Path("models")).ensure_model("yolo11n.pt")
    b = YoloBackend(model_path, ModelManager.select_device(),
                    confidence=0.35, imgsz=640, frame_interval=2)
    yield b
    b.close()


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "detectors:\n  person:\n    enabled: true\n"
        "  vehicle:\n    enabled: true\n  animal:\n    enabled: true\n",
        encoding="utf-8",
    )
    return load_config(p)


def bus_frame(seq: int) -> Frame:
    image = cv2.imread(str(ASSETS / "bus.jpg"))
    assert image is not None
    return Frame(image=image, timestamp=float(seq), seq=seq)


def test_detects_persons_and_bus(backend: YoloBackend) -> None:
    detections = backend.infer(bus_frame(0))
    categories = {d.category for d in detections}
    assert "person" in categories
    assert "vehicle" in categories
    labels = {d.label for d in detections}
    assert "bus" in labels
    for d in detections:
        assert 0.0 < d.confidence <= 1.0
        x1, y1, x2, y2 = d.bbox
        assert x1 < x2 and y1 < y2


def test_frame_interval_caches(backend: YoloBackend) -> None:
    start = backend.inference_count
    backend.infer(bus_frame(10))   # 10 % 2 == 0 → real inference
    backend.infer(bus_frame(11))   # in-between frame → cached
    backend.infer(bus_frame(12))   # real inference
    assert backend.inference_count == start + 2
    assert backend.infer(bus_frame(12)) is backend.infer(bus_frame(12))  # same seq


def test_tracking_ids_persist(backend: YoloBackend) -> None:
    first = backend.infer(bus_frame(100))
    second = backend.infer(bus_frame(102))
    ids_first = {d.track_id for d in first if d.category == "person"} - {None}
    ids_second = {d.track_id for d in second if d.category == "person"} - {None}
    assert ids_first, "tracking IDs were not assigned"
    assert ids_first & ids_second, "IDs should persist across the same scene"


def test_category_detectors_filter(backend: YoloBackend, config: Config) -> None:
    detectors = create_yolo_detectors(config, backend)
    assert [d.name for d in detectors] == ["person", "vehicle", "animal", "accessory", "weapon"]
    assert [d.display_name for d in detectors] == \
        ["Person Detection", "Vehicle Detection", "Animal Detection", "Accessory", "Weapon / Danger"]
    frame = bus_frame(200)
    person_dets = detectors[0].process(frame)
    vehicle_dets = detectors[1].process(frame)
    assert person_dets and all(d.category == "person" for d in person_dets)
    assert vehicle_dets and all(d.category == "vehicle" for d in vehicle_dets)


def test_class_map_labels_english() -> None:
    assert COCO_CLASS_MAP[0] == ("person", "person")
    assert COCO_CLASS_MAP[5] == ("vehicle", "bus")
    assert COCO_CLASS_MAP[16] == ("animal", "dog")


def test_accessory_classes_mapped() -> None:
    from ai.yolo import COCO_CLASS_MAP, create_yolo_detectors
    from core.config import Config
    from pathlib import Path

    # backpack/umbrella/handbag/suitcase mapped to the "accessory" category.
    assert COCO_CLASS_MAP[24] == ("accessory", "backpack")
    assert COCO_CLASS_MAP[25][0] == "accessory"
    cfg = Config(Path("config/default.yaml"))
    names = [d.name for d in create_yolo_detectors(cfg, backend=object())]
    assert "accessory" in names
