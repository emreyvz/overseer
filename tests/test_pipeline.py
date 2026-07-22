import time
from pathlib import Path

import numpy as np
import pytest

from camera.frame_buffer import Frame, FrameBuffer
from camera.health import HealthMonitor
from core.config import Config, load_config
from core.pipeline import AnalysisResult, AnalysisWorker, EventRecorder, EventThrottle
from events.bus import EventBus
from events.types import Event, EventType
from plugins.base import BaseDetector, Detection
from plugins.manager import PluginManager
from storage.database import Database
from storage.snapshots import SnapshotService


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "detectors:\n  fakeperson:\n    enabled: true\n"
        "  motion:\n    event_threshold_percent: 0.5\n"
        "events:\n  throttle_seconds: 0.3\n  snapshot_on_event: true\n"
        "camera:\n  freeze_timeout: 10.0\n",
        encoding="utf-8",
    )
    return load_config(p)


class FakePersonDetector(BaseDetector):
    name = "fakeperson"
    display_name = "Fake Person"

    def process(self, frame: Frame) -> list[Detection]:
        return [Detection(label="person", confidence=0.9, bbox=(1, 1, 10, 10),
                          category="person", track_id=1)]


def make_frame(seq: int) -> Frame:
    rng = np.random.default_rng(seq)
    img = rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)
    return Frame(image=img, timestamp=time.time(), seq=seq)


def run_worker_over(frames: list[Frame], config: Config,
                    tmp_path: Path) -> tuple[list[AnalysisResult], list[Event]]:
    buffer = FrameBuffer(maxsize=len(frames) + 1)
    plugins = PluginManager()
    plugins.register(FakePersonDetector(config))
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(None, events.append)
    results: list[AnalysisResult] = []
    worker = AnalysisWorker(
        buffer, plugins, HealthMonitor(freeze_timeout=10.0), bus, config,
        on_result=results.append, snapshots=SnapshotService(tmp_path / "snaps"),
    )
    worker.source_id = 42
    for f in frames:
        buffer.put(f)
    worker.start()
    deadline = time.time() + 10
    while len(results) < len(frames) and time.time() < deadline:
        time.sleep(0.02)
    worker.stop()
    worker.join(timeout=5)
    return results, events


def test_results_and_person_event(config: Config, tmp_path: Path) -> None:
    results, events = run_worker_over([make_frame(i) for i in range(3)], config, tmp_path)
    assert len(results) == 3
    assert results[0].detections["fakeperson"][0].label == "person"
    assert results[0].metrics.brightness > 0
    person_events = [e for e in events if e.type is EventType.PERSON]
    assert len(person_events) == 1  # throttle: single event across 3 frames
    assert person_events[0].source_id == 42
    assert person_events[0].confidence == 0.9
    snapshot_path = person_events[0].metadata.get("snapshot_path")
    assert snapshot_path is not None and Path(snapshot_path).exists()


def test_throttle_allows_after_interval() -> None:
    throttle = EventThrottle(interval=10.0)
    assert throttle.allow("PERSON:person", now=0.0) is True
    assert throttle.allow("PERSON:person", now=5.0) is False
    assert throttle.allow("VEHICLE:araba", now=5.0) is True  # different key
    assert throttle.allow("PERSON:person", now=10.5) is True


def test_event_recorder_persists(config: Config, tmp_path: Path) -> None:
    db = Database(tmp_path / "e.db")
    bus = EventBus()
    recorder = EventRecorder(db, bus)
    bus.publish(Event(type=EventType.PERSON, timestamp=time.time(), source_id=1,
                      label="person", confidence=0.8, bbox=(0, 0, 5, 5),
                      metadata={"snapshot_path": "x/y.jpg"}))
    stored = db.list_events()
    assert len(stored) == 1
    assert stored[0].snapshot_path == "x/y.jpg"
    recorder.close()
    bus.publish(Event(type=EventType.MOTION, timestamp=time.time(), source_id=1,
                      label="motion"))
    assert len(db.list_events()) == 1  # does not write after close
    db.close()


def test_fps_measured(config: Config, tmp_path: Path) -> None:
    results, _ = run_worker_over([make_frame(i) for i in range(5)], config, tmp_path)
    assert results[-1].fps >= 0.0
    assert results[-1].inference_ms >= 0.0


def test_worker_survives_bad_frame(config: Config, tmp_path: Path) -> None:
    # A 1-D image makes cv2.cvtColor inside compute_metrics raise, simulating
    # a malformed frame. The worker must log and continue instead of dying.
    bad_frame = Frame(image=np.zeros((4,), dtype=np.uint8), timestamp=time.time(), seq=0)
    good_frame = make_frame(1)
    buffer = FrameBuffer(maxsize=2)
    plugins = PluginManager()
    plugins.register(FakePersonDetector(config))
    bus = EventBus()
    results: list[AnalysisResult] = []
    worker = AnalysisWorker(
        buffer, plugins, HealthMonitor(freeze_timeout=10.0), bus, config,
        on_result=results.append, snapshots=SnapshotService(tmp_path / "snaps"),
    )
    worker.source_id = 1
    buffer.put(bad_frame)
    buffer.put(good_frame)
    worker.start()
    deadline = time.time() + 10
    while len(results) < 1 and time.time() < deadline:
        time.sleep(0.02)
    worker.stop()
    worker.join(timeout=5)
    # The bad frame produced no result but did not kill the worker thread;
    # the subsequent good frame was still processed.
    assert len(results) == 1
    assert results[0].frame.seq == 1
    assert not worker.is_alive()


def test_environment_readings_and_event(config: Config, tmp_path: Path) -> None:
    from plugins.analyzer import (
        AnalyzerEvent, AnalyzerReading, BaseAnalyzer, EnvironmentContext,
    )
    from plugins.analyzer_manager import AnalyzerManager

    class FakeWeather(BaseAnalyzer):
        name = "weather"
        display_name = "Weather"

        def __init__(self, cfg: Config) -> None:
            super().__init__(cfg)
            self._first = True

        def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
            event = None
            if self._first:
                self._first = False
                event = AnalyzerEvent(label="Weather changed: Foggy")
            return AnalyzerReading(
                values={"fog_probability": 0.9, "visibility": 0.1},
                labels={"weather": "Foggy"}, event=event,
            )

    analyzers = AnalyzerManager()
    analyzers.register(FakeWeather(config))

    buffer = FrameBuffer(maxsize=5)
    plugins = PluginManager()
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(None, events.append)
    results: list[AnalysisResult] = []
    worker = AnalysisWorker(
        buffer, plugins, HealthMonitor(freeze_timeout=10.0), bus, config,
        on_result=results.append, analyzers=analyzers,
    )
    for i in range(3):
        buffer.put(make_frame(i))
    worker.start()
    deadline = time.time() + 10
    while len(results) < 3 and time.time() < deadline:
        time.sleep(0.02)
    worker.stop()
    worker.join(timeout=5)

    assert results[0].environment.labels["weather"] == "Foggy"
    assert results[0].environment.values["fog_probability"] == 0.9
    env_events = [e for e in events if e.type is EventType.ENVIRONMENT]
    assert len(env_events) == 1
    assert env_events[0].label == "Weather changed: Foggy"


@pytest.fixture()
def config_interval2(tmp_path: Path) -> Config:
    p = tmp_path / "c2.yaml"
    p.write_text(
        "detectors:\n  fakeperson:\n    enabled: true\n"
        "  motion:\n    event_threshold_percent: 0.5\n"
        "events:\n  throttle_seconds: 0.3\n  snapshot_on_event: true\n"
        "camera:\n  freeze_timeout: 10.0\n"
        "analyzers:\n  frame_interval: 2\n",
        encoding="utf-8",
    )
    return load_config(p)


def test_analyzer_interval_gate_and_cache(config_interval2: Config, tmp_path: Path) -> None:
    from plugins.analyzer import AnalyzerReading, BaseAnalyzer, EnvironmentContext
    from plugins.analyzer_manager import AnalyzerManager

    class CountingAnalyzer(BaseAnalyzer):
        name = "counter"
        display_name = "Counter"

        def __init__(self, cfg: Config) -> None:
            super().__init__(cfg)
            self.calls = 0

        def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
            self.calls += 1
            return AnalyzerReading(
                values={"tick": float(self.calls)},
                labels={"weather": f"W{self.calls}"},
            )

    analyzer = CountingAnalyzer(config_interval2)
    analyzers = AnalyzerManager()
    analyzers.register(analyzer)

    buffer = FrameBuffer(maxsize=5)
    plugins = PluginManager()
    bus = EventBus()
    results: list[AnalysisResult] = []
    worker = AnalysisWorker(
        buffer, plugins, HealthMonitor(freeze_timeout=10.0), bus, config_interval2,
        on_result=results.append, analyzers=analyzers,
    )
    for i in range(4):
        buffer.put(make_frame(i))
    worker.start()
    deadline = time.time() + 10
    while len(results) < 4 and time.time() < deadline:
        time.sleep(0.02)
    worker.stop()
    worker.join(timeout=5)

    assert len(results) == 4
    # With frame_interval=2, only seq 0 and seq 2 pass the gate.
    assert analyzer.calls == 2
    # seq 1 was skipped -> cached readings from seq 0 reused, not blanked.
    assert results[1].environment.values["tick"] == results[0].environment.values["tick"]
    assert results[1].environment.labels["weather"] == results[0].environment.labels["weather"]
    # seq 2 passes the gate again -> a genuinely new reading.
    assert results[2].environment.values["tick"] != results[0].environment.values["tick"]
    assert results[2].environment.values["tick"] == 2.0
    # seq 3 was skipped -> cache from seq 2 reused.
    assert results[3].environment == results[2].environment


def test_analyzer_gate_robust_to_dropped_seqs(
    config_interval2: Config, tmp_path: Path
) -> None:
    # FrameBuffer is drop-oldest; dropped frames make frame.seq non-contiguous
    # and can phase-lock to mostly-odd or mostly-even values. The gate must
    # key off how many frames the worker actually processed, not frame.seq
    # parity, or analyzers can silently starve.
    #
    # seqs 0, 1, 3, 5 (simulating drops of seq 2 and seq 4): the OLD
    # `frame.seq % interval == 0` gate only ever matches seq 0 (seq 1, 3, 5
    # are all odd) -> old call count would be 1. The NEW processed-frame
    # counter gates on the 1st and 3rd processed frames regardless of their
    # seq -> call count is 2.
    from plugins.analyzer import AnalyzerReading, BaseAnalyzer, EnvironmentContext
    from plugins.analyzer_manager import AnalyzerManager

    class CountingAnalyzer(BaseAnalyzer):
        name = "counter"
        display_name = "Counter"

        def __init__(self, cfg: Config) -> None:
            super().__init__(cfg)
            self.calls = 0
            self.seen_seqs: list[int] = []

        def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
            self.calls += 1
            self.seen_seqs.append(frame.seq)
            return AnalyzerReading(values={"tick": float(self.calls)})

    analyzer = CountingAnalyzer(config_interval2)
    analyzers = AnalyzerManager()
    analyzers.register(analyzer)

    buffer = FrameBuffer(maxsize=10)
    plugins = PluginManager()
    bus = EventBus()
    results: list[AnalysisResult] = []
    worker = AnalysisWorker(
        buffer, plugins, HealthMonitor(freeze_timeout=10.0), bus, config_interval2,
        on_result=results.append, analyzers=analyzers,
    )
    seqs = [0, 1, 3, 5]
    for seq in seqs:
        buffer.put(make_frame(seq))
    worker.start()
    deadline = time.time() + 10
    while len(results) < len(seqs) and time.time() < deadline:
        time.sleep(0.02)
    worker.stop()
    worker.join(timeout=5)

    assert len(results) == 4
    assert analyzer.calls == 2  # runs on the 1st and 3rd processed frames
    assert analyzer.seen_seqs == [0, 3]


def test_no_analyzers_keeps_phase1_behavior(config: Config, tmp_path: Path) -> None:
    buffer = FrameBuffer(maxsize=5)
    plugins = PluginManager()
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(None, events.append)
    results: list[AnalysisResult] = []
    worker = AnalysisWorker(
        buffer, plugins, HealthMonitor(freeze_timeout=10.0), bus, config,
        on_result=results.append,  # no analyzers
    )
    buffer.put(make_frame(0))
    worker.start()
    deadline = time.time() + 10
    while len(results) < 1 and time.time() < deadline:
        time.sleep(0.02)
    worker.stop()
    worker.join(timeout=5)

    assert results[0].environment.values == {}
    assert results[0].environment.labels == {}
    from events.types import EventType as _ET
    assert not any(e.type is _ET.ENVIRONMENT for e in events)


def test_media_consumers_offered(config: Config, tmp_path: Path) -> None:
    from camera.frame_buffer import Frame as _F

    class FakeRecorder:
        def __init__(self) -> None:
            self.offers: list[bool] = []

        def offer(self, frame: _F, motion_active: bool) -> None:
            self.offers.append(motion_active)

    class FakeTimelapse:
        def __init__(self) -> None:
            self.count = 0

        def offer(self, frame: _F) -> None:
            self.count += 1

    class FakeHeatmap:
        def __init__(self) -> None:
            self.accumulated = 0

        def accumulate(self, mask) -> None:
            self.accumulated += 1

    from vision.motion import MotionDetector

    buffer = FrameBuffer(maxsize=5)
    plugins = PluginManager()
    plugins.register(MotionDetector(config))
    bus = EventBus()
    recorder = FakeRecorder()
    timelapse = FakeTimelapse()
    heatmap = FakeHeatmap()
    results: list[AnalysisResult] = []
    worker = AnalysisWorker(
        buffer, plugins, HealthMonitor(freeze_timeout=10.0), bus, config,
        on_result=results.append, recorder=recorder, timelapse=timelapse,
        heatmap=heatmap,
    )
    for i in range(3):
        buffer.put(make_frame(i))
    worker.start()
    deadline = time.time() + 10
    while len(results) < 3 and time.time() < deadline:
        time.sleep(0.02)
    worker.stop()
    worker.join(timeout=5)
    assert len(recorder.offers) == 3
    assert timelapse.count == 3
    assert heatmap.accumulated >= 1  # motion detector produced a mask


def test_disabled_motion_detector_ignored(config: Config, tmp_path: Path) -> None:
    """A disabled MotionDetector must not feed stale (frozen) mask/percent
    into the heatmap or recorder: PluginManager.process_frame() skips
    disabled detectors, so last_mask/motion_percent stop updating the moment
    the detector is disabled, but they still hold their last live values."""
    from vision.motion import MotionDetector

    class FakeHeatmap:
        def __init__(self) -> None:
            self.accumulated = 0

        def accumulate(self, mask) -> None:
            self.accumulated += 1

    class FakeRecorder:
        def __init__(self) -> None:
            self.offers: list[bool] = []

        def offer(self, frame: Frame, motion_active: bool) -> None:
            self.offers.append(motion_active)

    buffer = FrameBuffer(maxsize=10)
    plugins = PluginManager()
    motion = MotionDetector(config)
    plugins.register(motion)
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(None, events.append)
    heatmap = FakeHeatmap()
    recorder = FakeRecorder()
    results: list[AnalysisResult] = []
    worker = AnalysisWorker(
        buffer, plugins, HealthMonitor(freeze_timeout=10.0), bus, config,
        on_result=results.append, heatmap=heatmap, recorder=recorder,
    )
    # First 3 frames with motion enabled: let the detector genuinely
    # populate a non-trivial last_mask/motion_percent (random noise frames
    # make MOG2 see near-full-frame foreground, so motion_active is True).
    for i in range(3):
        buffer.put(make_frame(i))
    worker.start()
    deadline = time.time() + 10
    while len(results) < 3 and time.time() < deadline:
        time.sleep(0.02)
    accumulated_while_enabled = heatmap.accumulated
    assert accumulated_while_enabled == 3  # sanity: mask was populated each frame

    cutover = time.time()
    motion.enabled = False
    for i in range(3, 6):
        buffer.put(make_frame(i))
    while len(results) < 6 and time.time() < deadline:
        time.sleep(0.02)
    worker.stop()
    worker.join(timeout=5)

    assert len(results) == 6
    # No further heatmap accumulation once the detector is disabled: the
    # frozen mask from frame 2 must not keep feeding the heatmap.
    assert heatmap.accumulated == accumulated_while_enabled
    # motion_active must read False for the frames processed while disabled,
    # not the frozen (previously True) value.
    assert recorder.offers[3:] == [False, False, False]
    assert not any(e.type is EventType.MOTION and e.timestamp >= cutover for e in events)


def test_media_consumers_optional(config: Config, tmp_path: Path) -> None:
    buffer = FrameBuffer(maxsize=5)
    plugins = PluginManager()
    bus = EventBus()
    results: list[AnalysisResult] = []
    worker = AnalysisWorker(
        buffer, plugins, HealthMonitor(freeze_timeout=10.0), bus, config,
        on_result=results.append,  # no media consumers
    )
    buffer.put(make_frame(0))
    worker.start()
    deadline = time.time() + 10
    while len(results) < 1 and time.time() < deadline:
        time.sleep(0.02)
    worker.stop()
    worker.join(timeout=5)
    assert len(results) == 1  # Phase 1/2A behavior intact


def test_analyzer_event_type_maps_to_specific_event(config: Config, tmp_path: Path) -> None:
    from events.types import EventType as _ET
    from plugins.analyzer import (
        AnalyzerEvent, AnalyzerReading, BaseAnalyzer, EnvironmentContext,
    )
    from plugins.analyzer_manager import AnalyzerManager

    class FakeLightning(BaseAnalyzer):
        name = "lightning"
        display_name = "Lightning"

        def __init__(self, cfg: Config) -> None:
            super().__init__(cfg)
            self._fired = False

        def analyze(self, frame: Frame, ctx: EnvironmentContext) -> AnalyzerReading:
            event = None
            if not self._fired:
                self._fired = True
                event = AnalyzerEvent(label="Lightning", event_type=_ET.LIGHTNING)
            return AnalyzerReading(values={}, event=event)

    analyzers = AnalyzerManager()
    analyzers.register(FakeLightning(config))
    buffer = FrameBuffer(maxsize=5)
    bus = EventBus()
    events: list[Event] = []
    bus.subscribe(None, events.append)
    results: list[AnalysisResult] = []
    worker = AnalysisWorker(
        buffer, PluginManager(), HealthMonitor(freeze_timeout=10.0), bus, config,
        on_result=results.append, analyzers=analyzers,
    )
    buffer.put(make_frame(0))
    worker.start()
    deadline = time.time() + 10
    while len(results) < 1 and time.time() < deadline:
        time.sleep(0.02)
    worker.stop()
    worker.join(timeout=5)
    lightning = [e for e in events if e.type is _ET.LIGHTNING]
    assert len(lightning) == 1
    assert lightning[0].label == "Lightning"
    assert not any(e.type is _ET.ENVIRONMENT for e in events)
