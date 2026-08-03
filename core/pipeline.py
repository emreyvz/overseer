"""Analysis worker thread: runs plugins, generates throttled events, feeds the UI."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import cv2
from loguru import logger

from camera.frame_buffer import Frame, FrameBuffer
from camera.health import HealthMonitor
from core.config import Config
from events.bus import EventBus
from events.types import Event, EventType
from plugins.analyzer import AnalyzerEvent, EnvironmentReadings
from plugins.analyzer_manager import AnalyzerManager
from plugins.base import Detection
from plugins.manager import PluginManager
from storage.database import Database
from storage.snapshots import SnapshotService
from vision.metrics import ImageMetrics, compute_metrics

_CATEGORY_EVENTS: dict[str, EventType] = {
    "motion": EventType.MOTION,
    "person": EventType.PERSON,
    "vehicle": EventType.VEHICLE,
    "animal": EventType.ANIMAL,
}
_HEALTH_LABELS: dict[str, str] = {
    "frozen": "frozen frame",
    "no_frames": "stream lost",
    "recovered": "stream recovered",
}


def _forensic_inputs(
    detections: dict[str, list[Detection]],
) -> tuple[list[Detection], list[Detection]]:
    persons: list[Detection] = []
    accessories: list[Detection] = []
    for dets in detections.values():
        for det in dets:
            if det.category == "person" and det.track_id is not None:
                persons.append(det)
            elif det.category == "accessory":
                accessories.append(det)
    return persons, accessories


def _zone_inputs(detections: dict[str, list[Detection]]) -> list[Detection]:
    out: list[Detection] = []
    for dets in detections.values():
        for det in dets:
            if det.category in ("person", "vehicle") and det.track_id is not None:
                out.append(det)
    return out


class EventThrottle:
    def __init__(self, interval: float = 10.0) -> None:
        self._interval = interval
        self._last: dict[str, float] = {}

    def allow(self, key: str, now: float) -> bool:
        last = self._last.get(key)
        if last is not None and now - last < self._interval:
            return False
        self._last[key] = now
        return True


@dataclass
class AnalysisResult:
    frame: Frame
    detections: dict[str, list[Detection]]
    metrics: ImageMetrics
    motion_percent: float
    fps: float
    inference_ms: float
    environment: EnvironmentReadings = field(default_factory=EnvironmentReadings)
    tracklets: list = field(default_factory=list)
    zones: list = field(default_factory=list)


class AnalysisWorker(threading.Thread):
    def __init__(
        self,
        buffer: FrameBuffer,
        plugins: PluginManager,
        health: HealthMonitor,
        bus: EventBus,
        config: Config,
        on_result: Callable[[AnalysisResult], None],
        snapshots: SnapshotService | None = None,
        analyzers: AnalyzerManager | None = None,
        # Loosely typed as object | None so this module doesn't need a circular
        # import on storage/vision; callers pass real Recorder/TimelapseWriter/
        # MotionHeatmap instances. Treated as duck-typed for offer()/accumulate().
        recorder: object | None = None,
        timelapse: object | None = None,
        heatmap: object | None = None,
        forensic: object | None = None,
        zones: object | None = None,
        trajectory: object | None = None,
        pose: object | None = None,
        objects: object | None = None,
    ) -> None:
        super().__init__(daemon=True, name="AnalysisWorker")
        self._buffer = buffer
        self._plugins = plugins
        self._health = health
        self._bus = bus
        self._config = config
        self._on_result = on_result
        self._snapshots = snapshots
        self._analyzers = analyzers
        self._analyzer_interval = max(1, int(config.get("analyzers.frame_interval", 2)))
        self._analyzer_frames = 0
        self._last_environment = EnvironmentReadings()
        self._recorder = recorder
        self._timelapse = timelapse
        self._heatmap = heatmap
        self._forensic = forensic
        self._zones = zones
        self._trajectory = trajectory
        self._pose = pose
        self._objects = objects
        self._stop_event = threading.Event()
        self._prev_health: set[str] = set()
        self._throttle = EventThrottle(
            interval=float(config.get("events.throttle_seconds", 10.0))
        )
        self._motion_event_threshold = float(
            config.get("detectors.motion.event_threshold_percent", 0.5)
        )
        self._snapshot_on_event = bool(config.get("events.snapshot_on_event", True))
        self._fps = 0.0
        self._last_frame_time: float | None = None
        self.source_id: int | None = None

    def run(self) -> None:
        from core.thread_priority import set_current_thread_priority, BELOW_NORMAL
        set_current_thread_priority(BELOW_NORMAL)   # yield CPU/GIL to the 30fps display path under load
        while not self._stop_event.is_set():
            frame = self._buffer.get(timeout=0.5)
            now = time.time()
            if frame is None:
                self._publish_health(self._health.check(now), now)
                continue
            try:
                started = time.perf_counter()
                detections = self._plugins.process_frame(frame)
                inference_ms = (time.perf_counter() - started) * 1000.0
                metrics = compute_metrics(frame.image)
                self._health.observe(frame)
                self._publish_health(self._health.check(now), now)
                # Treat a disabled (or absent) motion detector as producing no
                # motion at all: PluginManager.process_frame() skips disabled
                # detectors, so a disabled MotionDetector's last_mask/
                # motion_percent are frozen at their last live values, not
                # reset. Without this gate, the pipeline would keep
                # accumulating a stale mask into the heatmap and could latch
                # motion-mode recording on forever after the user disables
                # "Motion Detection".
                motion = self._plugins.get("motion")
                motion_enabled = motion is not None and motion.enabled
                motion_percent = self._motion_percent(motion, motion_enabled)
                self._update_fps(now)
                self._publish_detections(frame, detections, motion_percent, now)
                motion_active = motion_enabled and \
                    motion_percent >= self._motion_event_threshold
                if self._heatmap is not None and motion_enabled:
                    mask = getattr(motion, "last_mask", None)
                    if mask is not None:
                        self._heatmap.accumulate(mask)
                if self._recorder is not None:
                    self._recorder.offer(frame, motion_active)
                if self._timelapse is not None:
                    self._timelapse.offer(frame)
                if self._analyzers is not None:
                    # Gate on a worker-local processed-frame counter, not frame.seq
                    # parity: FrameBuffer is drop-oldest, so if drops phase-lock the
                    # worker could otherwise see mostly odd (or mostly even) seqs
                    # and starve or double-run the analyzers.
                    if self._analyzer_frames % self._analyzer_interval == 0:
                        readings, env_events = self._analyzers.analyze_frame(self._analyzer_frame(frame), metrics)
                        self._last_environment = readings
                        self._publish_environment(env_events, frame, now)   # full-res frame for the snapshot
                    self._analyzer_frames += 1
                tracklets: list = []
                if self._forensic is not None:
                    persons, accessories = _forensic_inputs(detections)
                    tracklets = self._forensic.offer(
                        self.source_id, frame, persons, accessories)
                zone_views: list = []
                if self._zones is not None:
                    for ze in self._zones.process(
                            _zone_inputs(detections), now, self.source_id):
                        if not self._throttle.allow(
                                f"{ze.event_type.name}:{ze.zone_id}:{ze.track_id}", now):
                            continue
                        metadata = self._snapshot_metadata(
                            frame, prefix=ze.event_type.name.lower())
                        metadata.update(ze.metadata)
                        self._bus.publish(Event(
                            type=ze.event_type, timestamp=now,
                            source_id=self.source_id, label=ze.label,
                            metadata=metadata))
                    zone_views = self._zones.snapshot()
                self._publish_trajectory(detections, frame, now)
                self._publish_pose(detections, frame, now)
                self._publish_objects(detections, frame, now)
                try:
                    self._on_result(AnalysisResult(
                        frame=frame, detections=detections, metrics=metrics,
                        motion_percent=motion_percent, fps=self._fps,
                        inference_ms=inference_ms,
                        environment=self._last_environment,
                        tracklets=tracklets,
                        zones=zone_views,
                    ))
                except Exception:
                    logger.exception("on_result callback failed")
            except Exception:
                logger.exception("frame processing failed (seq={})", frame.seq)

    def _analyzer_frame(self, frame: Frame) -> Frame:
        """A downscaled copy for the scene analyzers. The wind analyzer runs DENSE optical flow, which
        is ~340 ms on a 1080p frame and the single biggest per-frame cost; at ~480 px wide it is ~20 ms,
        and the scene-condition estimates (fog / rain / wind / weather / day-night) are ratios that read
        the same on the smaller frame. This is the main throughput fix."""
        img = frame.image
        h, w = img.shape[:2]
        if w <= 480:
            return frame
        tw = 480
        th = max(1, round(h * tw / w))
        return Frame(image=cv2.resize(img, (tw, th)), timestamp=frame.timestamp, seq=frame.seq)

    def _motion_percent(self, motion: object | None, motion_enabled: bool) -> float:
        if not motion_enabled:
            return 0.0
        return float(getattr(motion, "motion_percent", 0.0))

    def _update_fps(self, now: float) -> None:
        if self._last_frame_time is not None:
            dt = now - self._last_frame_time
            if dt > 0:
                instant = 1.0 / dt
                self._fps = instant if self._fps == 0.0 else \
                    0.9 * self._fps + 0.1 * instant
        self._last_frame_time = now

    def _snapshot_metadata(self, frame: Frame, prefix: str) -> dict[str, object]:
        if not (self._snapshot_on_event and self._snapshots is not None):
            return {}
        try:
            path = self._snapshots.save(frame.image, prefix=prefix)
            return {"snapshot_path": str(path)}
        except OSError:
            logger.exception("snapshot failed")
            return {}

    def _publish_detections(
        self,
        frame: Frame,
        detections: dict[str, list[Detection]],
        motion_percent: float,
        now: float,
    ) -> None:
        best_by_label: dict[tuple[EventType, str], Detection] = {}
        for plugin_detections in detections.values():
            for det in plugin_detections:
                etype = _CATEGORY_EVENTS.get(det.category)
                if etype is None:
                    continue
                if etype is EventType.MOTION and \
                        motion_percent < self._motion_event_threshold:
                    continue
                key = (etype, det.label)
                current = best_by_label.get(key)
                if current is None or det.confidence > current.confidence:
                    best_by_label[key] = det
        for (etype, label), det in best_by_label.items():
            if not self._throttle.allow(f"{etype.name}:{label}", now):
                continue
            metadata = self._snapshot_metadata(frame, prefix=etype.name.lower())
            if etype is EventType.MOTION:
                metadata["motion_percent"] = round(motion_percent, 2)
            self._bus.publish(Event(
                type=etype, timestamp=now, source_id=self.source_id, label=label,
                confidence=det.confidence, bbox=det.bbox, metadata=metadata,
            ))

    def _publish_environment(
        self, events: list[AnalyzerEvent], frame: Frame, now: float
    ) -> None:
        for event in events:
            etype = event.event_type or EventType.ENVIRONMENT
            if not self._throttle.allow(f"{etype.name}:{event.label}", now):
                continue
            metadata = self._snapshot_metadata(frame, prefix=etype.name.lower())
            metadata.update(event.metadata)
            self._bus.publish(Event(
                type=etype, timestamp=now, source_id=self.source_id,
                label=event.label, metadata=metadata,
            ))

    def _publish_trajectory(self, detections: dict[str, list[Detection]],
                            frame: Frame, now: float) -> None:
        if self._trajectory is None:
            return
        for te in self._trajectory.process(_zone_inputs(detections), now):
            if not self._throttle.allow(f"{te.event_type.name}:{te.track_id}", now):
                continue
            metadata = self._snapshot_metadata(frame, prefix=te.event_type.name.lower())
            metadata.update(te.metadata)
            metadata.setdefault("track_id", te.track_id)
            self._bus.publish(Event(
                type=te.event_type, timestamp=now, source_id=self.source_id,
                label=te.label, metadata=metadata))

    def _publish_pose(self, detections: dict[str, list[Detection]],
                      frame: Frame, now: float) -> None:
        if self._pose is None:
            return
        persons, _ = _forensic_inputs(detections)
        for ev in self._pose.process(persons, now):
            self._publish_track_event(ev, frame, now)

    def _publish_objects(self, detections: dict[str, list[Detection]],
                         frame: Frame, now: float) -> None:
        if self._objects is None:
            return
        persons, accessories = _forensic_inputs(detections)
        for ev in self._objects.process(accessories, persons, now):
            self._publish_track_event(ev, frame, now)

    def _publish_track_event(self, ev: object, frame: Frame, now: float) -> None:
        etype = ev.event_type
        if not self._throttle.allow(f"{etype.name}:{ev.track_id}", now):
            return
        metadata = self._snapshot_metadata(frame, prefix=etype.name.lower())
        metadata.update(ev.metadata)
        metadata.setdefault("track_id", ev.track_id)
        self._bus.publish(Event(
            type=etype, timestamp=now, source_id=self.source_id, label=ev.label,
            metadata=metadata))

    def _publish_health(self, issues: list[str], now: float) -> None:
        # Emit CAMERA_HEALTH only on STATE TRANSITIONS — a newly-appeared issue,
        # or recovery once all issues clear — rather than on every check, so a
        # sustained outage produces one event instead of one per interval.
        current = set(issues)
        for issue in current - self._prev_health:
            self._bus.publish(Event(
                type=EventType.CAMERA_HEALTH, timestamp=now,
                source_id=self.source_id,
                label=_HEALTH_LABELS.get(issue, issue),
                metadata={"issue": issue},
            ))
        if self._prev_health and not current:
            self._bus.publish(Event(
                type=EventType.CAMERA_HEALTH, timestamp=now,
                source_id=self.source_id, label=_HEALTH_LABELS["recovered"],
                metadata={"issue": "recovered"},
            ))
        self._prev_health = current

    def stop(self) -> None:
        self._stop_event.set()


class EventRecorder:
    """Persists every published event to the database."""

    def __init__(self, db: Database, bus: EventBus) -> None:
        self._db = db
        self._unsubscribe = bus.subscribe(None, self._on_event)

    def _on_event(self, event: Event) -> None:
        snapshot_path = event.metadata.get("snapshot_path")
        try:
            self._db.add_event(
                event,
                snapshot_path=str(snapshot_path) if snapshot_path else None,
            )
        except Exception:
            logger.exception("failed to persist event")

    def close(self) -> None:
        self._unsubscribe()
