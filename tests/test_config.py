from pathlib import Path

import pytest

from core.config import load_config

YAML_CONTENT = """
app:
  log_level: INFO
camera:
  buffer_size: 5
"""


@pytest.fixture()
def config_file(tmp_path: Path) -> Path:
    p = tmp_path / "test.yaml"
    p.write_text(YAML_CONTENT, encoding="utf-8")
    return p


def test_get_dotted_key(config_file: Path) -> None:
    cfg = load_config(config_file)
    assert cfg.get("camera.buffer_size") == 5
    assert cfg.get("app.log_level") == "INFO"


def test_get_missing_returns_default(config_file: Path) -> None:
    cfg = load_config(config_file)
    assert cfg.get("nope.nothing", 42) == 42
    assert cfg.get("nope.nothing") is None


def test_set_dotted_key(config_file: Path) -> None:
    cfg = load_config(config_file)
    cfg.set("camera.buffer_size", 9)
    assert cfg.get("camera.buffer_size") == 9
    cfg.set("brand.new.key", "x")
    assert cfg.get("brand.new.key") == "x"


def test_reload_detects_change(config_file: Path) -> None:
    cfg = load_config(config_file)
    assert cfg.reload() is False  # no change yet
    config_file.write_text(YAML_CONTENT.replace("5", "7"), encoding="utf-8")
    import os
    os.utime(config_file, (0, 9999999999))  # guarantee mtime differs
    assert cfg.reload() is True
    assert cfg.get("camera.buffer_size") == 7


def test_default_yaml_loads() -> None:
    cfg = load_config(Path("config/default.yaml"))
    assert cfg.get("camera.buffer_size") == 5
    assert cfg.get("detectors.yolo.model") == "yolo11s.pt"
