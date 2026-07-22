import numpy as np

from camera.frame_buffer import Frame, FrameBuffer
from camera.health import HealthMonitor
from core.config import Config
from core.pipeline import AnalysisWorker
from events.bus import EventBus
from events.types import EventType
from plugins.base import Detection
from plugins.manager import PluginManager


class _TrajSpy:
    def __init__(self) -> None:
        self.calls: list = []

    def process(self, dets, now):
        self.calls.append([d.track_id for d in dets])
        from trajectory.monitor import TrajectoryEvent
        return [TrajectoryEvent(EventType.RUNNING, 7, "running", {"speed": 300.0})]


def test_worker_publishes_trajectory_events(tmp_path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("events:\n  throttle_seconds: 0.0\n  snapshot_on_event: false\n",
                 encoding="utf-8")
    config = Config(p)
    bus = EventBus()
    got: list = []
    bus.subscribe(EventType.RUNNING, got.append)
    spy = _TrajSpy()
    worker = AnalysisWorker(
        FrameBuffer(), PluginManager(), HealthMonitor(), bus, config,
        on_result=lambda r: None, trajectory=spy)
    worker.source_id = 1
    # drive one frame's worth of the trajectory publish path directly:
    dets = {"person": [Detection("person", 0.9, (0, 0, 10, 10), "person", track_id=7)]}
    worker._publish_trajectory(dets, Frame(image=np.zeros((4, 4, 3), np.uint8),
                                           timestamp=0.0, seq=0), now=1.0)
    assert spy.calls == [[7]]
    assert [e.type for e in got] == [EventType.RUNNING]
