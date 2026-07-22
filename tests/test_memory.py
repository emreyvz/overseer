"""Short-run leak check: RSS must not grow unbounded while streaming."""
import gc
import time
from pathlib import Path

import psutil

from camera.frame_buffer import FrameBuffer
from camera.health import HealthMonitor
from camera.stream_reader import StreamReader
from core.config import load_config
from core.pipeline import AnalysisWorker
from events.bus import EventBus
from plugins.manager import PluginManager
from vision.motion import MotionDetector


def test_streaming_memory_stable(mjpeg_server, tmp_path: Path) -> None:
    config_file = tmp_path / "c.yaml"
    config_file.write_text(
        "detectors:\n  motion:\n    enabled: true\n"
        "events:\n  snapshot_on_event: false\n",
        encoding="utf-8",
    )
    config = load_config(config_file)
    url = mjpeg_server(frame_interval=0.005)
    buffer = FrameBuffer(maxsize=5)
    plugins = PluginManager()
    plugins.register(MotionDetector(config))
    worker = AnalysisWorker(buffer, plugins, HealthMonitor(), EventBus(), config,
                            on_result=lambda r: None)
    reader = StreamReader(url, buffer)
    worker.start()
    reader.start()
    try:
        time.sleep(2.0)  # warm-up
        gc.collect()
        rss_before = psutil.Process().memory_info().rss
        time.sleep(8.0)
        gc.collect()
        rss_after = psutil.Process().memory_info().rss
    finally:
        reader.stop()
        worker.stop()
        reader.join(timeout=5)
        worker.join(timeout=5)
    growth_mb = (rss_after - rss_before) / (1024 * 1024)
    assert growth_mb < 60, f"RSS growth {growth_mb:.1f} MB — possible leak"
    assert worker.is_alive() is False and reader.is_alive() is False
