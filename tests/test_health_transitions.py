"""Phase 16: CAMERA_HEALTH is emitted on state transitions only, not per check."""
from __future__ import annotations

from pathlib import Path


def _worker(tmp_path: Path):
    from camera.frame_buffer import FrameBuffer
    from camera.health import HealthMonitor
    from core.config import load_config
    from core.pipeline import AnalysisWorker
    from events.bus import EventBus
    from plugins.manager import PluginManager
    from storage.snapshots import SnapshotService

    cfg = load_config(Path("config/default.yaml"))
    bus = EventBus()
    events: list = []
    bus.subscribe(None, events.append)
    worker = AnalysisWorker(FrameBuffer(maxsize=2), PluginManager(),
                            HealthMonitor(10.0), bus, cfg,
                            on_result=lambda r: None,
                            snapshots=SnapshotService(tmp_path / "s"))
    worker.source_id = 1
    return worker, events


def test_sustained_outage_emits_one_event(tmp_path: Path) -> None:
    from events.types import EventType

    worker, events = _worker(tmp_path)
    for t in (1.0, 11.0, 21.0, 31.0):          # same issue over many checks
        worker._publish_health(["no_frames"], t)
    health = [e for e in events if e.type is EventType.CAMERA_HEALTH]
    assert len(health) == 1 and health[0].metadata["issue"] == "no_frames"


def test_recovery_emits_recovered_event(tmp_path: Path) -> None:
    from events.types import EventType

    worker, events = _worker(tmp_path)
    worker._publish_health(["frozen"], 1.0)     # broken
    worker._publish_health([], 11.0)            # cleared -> recovered
    health = [e for e in events if e.type is EventType.CAMERA_HEALTH]
    issues = [e.metadata["issue"] for e in health]
    assert issues == ["frozen", "recovered"]


def test_new_issue_transition_emits(tmp_path: Path) -> None:
    from events.types import EventType

    worker, events = _worker(tmp_path)
    worker._publish_health(["no_frames"], 1.0)
    worker._publish_health(["no_frames"], 5.0)   # unchanged -> no new event
    worker._publish_health(["no_frames", "frozen"], 9.0)  # frozen newly appears
    health = [e for e in events if e.type is EventType.CAMERA_HEALTH]
    assert [e.metadata["issue"] for e in health] == ["no_frames", "frozen"]
