from pathlib import Path

import numpy as np
import pytest

from camera.frame_buffer import Frame
from core.config import Config, load_config
from plugins.base import BaseDetector, Detection
from plugins.manager import PluginManager


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "detectors:\n  fake:\n    enabled: true\n  disabledfake:\n    enabled: false\n",
        encoding="utf-8",
    )
    return load_config(p)


def make_frame() -> Frame:
    return Frame(image=np.zeros((32, 32, 3), dtype=np.uint8), timestamp=0.0, seq=0)


class FakeDetector(BaseDetector):
    name = "fake"
    display_name = "Sahte"

    def process(self, frame: Frame) -> list[Detection]:
        return [Detection(label="sahte", confidence=1.0, bbox=(0, 0, 1, 1),
                          category="motion")]


class DisabledDetector(BaseDetector):
    name = "disabledfake"
    display_name = "Closed Fake"

    def process(self, frame: Frame) -> list[Detection]:
        return [Detection(label="x", confidence=1.0, bbox=(0, 0, 1, 1),
                          category="motion")]


class BrokenDetector(BaseDetector):
    name = "broken"
    display_name = "Bozuk"

    def process(self, frame: Frame) -> list[Detection]:
        raise RuntimeError("boom")


def test_enabled_read_from_config(config: Config) -> None:
    assert FakeDetector(config).enabled is True
    assert DisabledDetector(config).enabled is False
    assert BrokenDetector(config).enabled is True  # not in config → default True


def test_register_and_duplicate(config: Config) -> None:
    mgr = PluginManager()
    det = FakeDetector(config)
    mgr.register(det)
    assert mgr.get("fake") is det
    assert mgr.get("offline") is None
    with pytest.raises(ValueError):
        mgr.register(FakeDetector(config))


def test_process_frame_runs_only_enabled(config: Config) -> None:
    mgr = PluginManager()
    mgr.register(FakeDetector(config))
    mgr.register(DisabledDetector(config))
    results = mgr.process_frame(make_frame())
    assert list(results.keys()) == ["fake"]
    assert results["fake"][0].label == "sahte"


def test_set_enabled(config: Config) -> None:
    mgr = PluginManager()
    mgr.register(FakeDetector(config))
    mgr.set_enabled("fake", False)
    assert mgr.process_frame(make_frame()) == {}


def test_broken_plugin_isolated(config: Config) -> None:
    mgr = PluginManager()
    mgr.register(BrokenDetector(config))
    mgr.register(FakeDetector(config))
    results = mgr.process_frame(make_frame())
    assert results["broken"] == []
    assert len(results["fake"]) == 1
