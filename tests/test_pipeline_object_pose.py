import numpy as np

from camera.frame_buffer import Frame, FrameBuffer
from camera.health import HealthMonitor
from core.config import Config
from core.pipeline import AnalysisWorker
from events.bus import EventBus
from events.types import EventType
from plugins.base import Detection
from plugins.manager import PluginManager


class _PoseSpy:
    def __init__(self) -> None:
        self.calls: list = []

    def process(self, persons, now):
        self.calls.append([p.track_id for p in persons])
        from pose.monitor import PoseEvent
        return [PoseEvent(EventType.FALLING, 7, "fall", {"aspect": 0.2})]


class _ObjSpy:
    def __init__(self) -> None:
        self.calls: list = []

    def process(self, accessories, persons, now):
        self.calls.append(([a.track_id for a in accessories],
                           [p.track_id for p in persons]))
        from objects.monitor import ObjectEvent
        return [ObjectEvent(EventType.ABANDONED_OBJECT, 5, "backpack",
                            {"label": "backpack"})]


def _worker(config, bus, pose=None, objects=None):
    return AnalysisWorker(FrameBuffer(), PluginManager(), HealthMonitor(), bus, config,
                          on_result=lambda r: None, pose=pose, objects=objects)


def test_worker_publishes_pose_and_object_events(tmp_path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("events:\n  throttle_seconds: 0.0\n  snapshot_on_event: false\n",
                 encoding="utf-8")
    config = Config(p)
    bus = EventBus()
    got: list = []
    bus.subscribe(None, got.append)
    pose, objs = _PoseSpy(), _ObjSpy()
    worker = _worker(config, bus, pose=pose, objects=objs)
    worker.source_id = 1
    dets = {
        "person": [Detection("person", 0.9, (0, 0, 10, 60), "person", track_id=7)],
        "accessory": [Detection("backpack", 0.9, (5, 5, 15, 15), "accessory",
                                track_id=5)],
    }
    frame = Frame(image=np.zeros((4, 4, 3), np.uint8), timestamp=0.0, seq=0)
    worker._publish_pose(dets, frame, now=1.0)
    worker._publish_objects(dets, frame, now=1.0)
    assert pose.calls == [[7]]
    assert objs.calls == [([5], [7])]
    types = {e.type for e in got}
    assert EventType.FALLING in types and EventType.ABANDONED_OBJECT in types
