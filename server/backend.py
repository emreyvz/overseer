"""Headless orchestrator: replicates ui/app.py wiring without Qt, bridging the
existing pipeline (camera -> AnalysisWorker -> EventBus/DB) to async web transports.

Thread model (per backend integration reference):
  StreamReader (thread) -> FrameBuffer (drop-oldest) -> AnalysisWorker (thread).
  on_result & bus callbacks fire ON THE WORKER THREAD -> we hop to the asyncio loop
  via loop.call_soon_threadsafe.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable

import cv2
import numpy as np

from alerts.engine import AlertEngine
from alerts.summary import EventSummarizer
from alerts.threat import ThreatScorer, escalate
from camera.file_reader import FileLoopReader
from camera.frame_buffer import FrameBuffer
from camera.health import HealthMonitor
from camera.stream_reader import StreamReader
from core.config import load_config
from core.pipeline import AnalysisResult, AnalysisWorker, EventRecorder
from events.bus import EventBus
from events.types import Event
from forensic.palette import dominant_color_name
from match.engine import SourceFrames
from match.rolling import RollingFrameStore
from match.types import Query
from objects.monitor import ObjectMonitor
from plugins.manager import PluginManager
from pose.monitor import PoseMonitor
from storage.database import Database
from storage.recorder import Recorder
from storage.snapshots import SnapshotService
from trajectory.monitor import TrajectoryMonitor
from trajectory.speed import SpeedEstimator
from vehicle.make import MakeClassifier
from vision.egomotion import EgoMotion
from vision.motion import MotionDetector
from zones.monitor import ZoneMonitor
from .clipenc import encode_clip
from .media import MediaLibrary
from .ooi import OOIManager
from .pose_kp import PoseKP
from .ptz import PTZController
from .thumbs import ThumbHub

log = logging.getLogger("overseer.server")

_CATEGORY_CLS = {"person": "person", "vehicle": "vehicle", "animal": "animal",
                 "accessory": "object", "motion": "object", "weapon": "object"}
_CLS_KLASS = {"person": "TRACKED", "vehicle": "VEHICLE", "animal": "ANIMAL", "object": "OBJECT"}
# What the subject of each event type is — drives the incident marker's shape/label on
# the annotated replay so the operator sees *what* triggered it at a glance.
_EVENT_KIND = {
    "VEHICLE": "vehicle", "ABANDONED_OBJECT": "object", "REMOVED_OBJECT": "object",
    "ANIMAL": "animal", "MOTION": "object",
}

Broadcaster = Callable[[dict[str, Any]], Awaitable[None]]


class Backend:
    def __init__(self, config_path: Path = Path("config/default.yaml"),
                 data_dir: Path | None = None) -> None:
        self.config = load_config(config_path)
        self.data_dir = data_dir or Path(str(self.config.get("app.data_dir", "data")))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(self.data_dir / "overseer.db")
        self._snap_dir = self.data_dir / "snapshots"
        self.snapshots = SnapshotService(self._snap_dir)
        self.bus = EventBus()
        self.event_recorder = EventRecorder(self.db, self.bus)  # persists every event

        # Alert engine (rules from DB; seed defaults once).
        self.db.seed_default_alert_rules(
            int(self.config.get("alerts.crowd_min", 8)),
            float(self.config.get("alerts.cooldown_seconds", 60.0)),
        )
        # On a fresh install, seed one public demo camera so the map isn't empty.
        self.db.seed_default_source()
        self.alert_engine = AlertEngine(EventSummarizer(), self._source_name)
        self.alert_engine.set_rules(self.db.list_alert_rules())
        # Correlated threat scoring: fuses co-occurring events per camera so dangerous
        # combinations escalate (and surface even when no single rule was configured).
        self.threat = ThreatScorer(
            window_s=float(self.config.get("threat.window_seconds", 25.0)))
        self._threat_synth_cooldown = float(self.config.get("threat.synth_cooldown_s", 30.0))
        self._last_threat_synth: dict[object, float] = {}
        self._ensure_coords()

        # Behavioural monitors (constructed once, reset per source).
        self.zones = ZoneMonitor(self.config)
        self.trajectory = TrajectoryMonitor(self.config)
        self.pose = PoseMonitor(self.config)
        self.objects = ObjectMonitor(self.config)
        self.speed = SpeedEstimator(
            meters_per_pixel=float(self.config.get("speed.meters_per_pixel", 0.05)),
            window=float(self.config.get("speed.window_seconds", 1.2)),
            ema_alpha=float(self.config.get("speed.ema_alpha", 0.4)),
            min_kmh=float(self.config.get("speed.min_kmh", 3.0)),
            max_kmh=float(self.config.get("speed.max_kmh", 300.0)),
            still_px=float(self.config.get("speed.still_px", 25.0)),
        )  # rough per-vehicle km/h estimate for the live overlay
        # Camera ego-motion: a dashcam drags the whole scene, so vehicle speeds must be measured
        # relative to the ground, not the frame. EgoMotion supplies the per-frame global shift
        # that SpeedEstimator subtracts; a fixed camera contributes nothing.
        self.ego = EgoMotion(
            width=int(self.config.get("egomotion.width", 320)),
            moving_flow=float(self.config.get("egomotion.moving_flow", 1.3)))
        self._cam_moving = False   # camera itself in motion (dashcam) — surfaced to the HUD
        # real-world heights (m) per vehicle subtype — the size reference that lets speed
        # auto-calibrate to depth (see SpeedEstimator). Keys are COCO labels.
        _ch = self.config.get("speed.class_heights", {}) or {}
        self._class_heights = {str(k): float(v) for k, v in _ch.items()}
        self._default_height = float(self.config.get("speed.default_height", 1.6))
        self.thumbs = ThumbHub(
            max_workers=int(self.config.get("thumbs.max_workers", 24)),
            cache_dir=self.data_dir / "thumbs",
        )  # per-camera preview relay + persistent cache
        # YouTube (and yt-dlp) sources are downloaded once and played on a loop like a
        # live camera, so the feed never expires the way a signed HLS URL does.
        self.media = MediaLibrary(
            self.data_dir / "media",
            max_height=int(self.config.get("media.max_height", 1080)),
            max_concurrent=int(self.config.get("media.max_concurrent_downloads", 1)),
        )
        # Live per-track plate reading (ANPR) for the vehicle tracking card. Runs off the
        # analysis thread, throttled per track; needs EasyOCR (ai-extras) or stays idle.
        from .plates import LivePlateReader
        self.plates = LivePlateReader(
            interval=float(self.config.get("match.anpr.live_interval", 2.5)))
        # Session roster: an anonymous, deduped registry of people + vehicles seen, with a
        # photo each (and plates for vehicles). Background cutouts use the YOLO-seg model.
        from match.seg_backend import YoloSegBackend
        from .roster import SessionRoster
        _seg_name = str(self.config.get("match.models.seg", "") or "")
        _roster_seg = YoloSegBackend(Path("models") / _seg_name) if _seg_name else None
        self.roster = SessionRoster(
            self.snapshots, self._snap_dir, _roster_seg,
            dedup_threshold=float(self.config.get("roster.dedup_threshold", 0.82)))
        # Vehicle make/brand classifier for roster profiles (CPU, off the GPU hot path).
        # Quiet unless its weights are present under models/ (uv run -m match.tools.export_models
        # --only carbrand). Confidence-gated so it never asserts a confident-but-wrong brand.
        self.make = MakeClassifier(
            Path("models") / str(self.config.get("vehicle.make.model", "vehicle_make.torchscript")),
            min_conf=float(self.config.get("vehicle.make.min_conf", 0.35)),
            min_margin=float(self.config.get("vehicle.make.min_margin", 0.10)),
            min_area=int(self.config.get("vehicle.make.min_area", 4096)))
        # Background brand reader for the live tracking card (classification is CPU-bound, so
        # it runs off-thread and the card reads the cached brand — never stalls the analysis).
        from .live_make import LiveMakeReader
        self.live_make = LiveMakeReader(
            self.make, interval=float(self.config.get("vehicle.make.live_interval", 4.0)))
        self._embed_lock = threading.Lock()   # serialize ReID encoder use (harvester vs search)
        self._roster_harvester = None
        self._roster_det = None
        self._roster_boot_lock = threading.Lock()  # guard boot-vs-connect harvester start race
        self._supercut_cache: dict[str, tuple[int, str]] = {}   # det_id -> (n legs, url)
        self._roster_fullres_last: dict[int, float] = {}  # per-source last full-res grab time
        self._roster_seek: dict[int, float] = {}          # rotating sample point in looped files
        self._prewarm_thumbs()
        self.ooi = OOIManager()   # object-of-interest visual tracker
        self.pose_kp = PoseKP()   # keypoint pose behaviours (hand-raise)
        self._pose_ctr = 0
        self._last_handraise = 0.0
        self._last_weapon = 0.0
        self._ooi_lost: dict[str, bool] = {}
        self._last_ooi_alert: dict[str, float] = {}
        self.ptz = PTZController()  # feature 13 — best-effort ONVIF PTZ
        from .ai_llm import LLMClient
        self.ai = LLMClient()       # GLM/OpenAI-compatible assistant layer
        self._yolo = None           # YoloBackend handle for 'look closer'
        self._clip_ring: deque = deque(maxlen=40)  # recent frames for incident clips
        # Live-but-stable appearance matching: a per-source rolling frame window + a
        # lazily-built engine (real ReID/ANPR/seg models if present, baseline otherwise).
        self.rolling = RollingFrameStore(
            window_seconds=float(self.config.get("match.window_seconds", 3.0)),
            max_frames=int(self.config.get("match.max_frames", 20)),
        )
        self.match_engine = None
        self.match_info: dict = {}

        self._reader: StreamReader | None = None
        self._worker: AnalysisWorker | None = None
        self._buffer: FrameBuffer | None = None
        self._health: HealthMonitor | None = None
        self._plugins: PluginManager | None = None
        self._recorder: Recorder | None = None
        self._source_id: int | None = None
        self._conn = "offline"
        self._lock = threading.RLock()

        self._latest_jpeg: bytes | None = None
        self._latest_img: Any = None
        self._last_frame_push = 0.0

        self._loop: Any = None
        self._broadcast: Broadcaster | None = None
        self._unsub = self.bus.subscribe(None, self._on_event)

    # ---- async bridge -------------------------------------------------
    def bind(self, loop: Any, broadcast: Broadcaster) -> None:
        self._loop = loop
        self._broadcast = broadcast
        # Fill the roster from ALL cameras in the background starting at boot — the operator
        # should see people/vehicles appear without having to open a camera first. The detector
        # load happens off the event loop so startup isn't blocked.
        threading.Thread(target=self._start_roster_harvester, name="RosterBoot", daemon=True).start()

    def _emit(self, msg: dict[str, Any]) -> None:
        if self._loop is None or self._broadcast is None:
            return
        try:
            self._loop.call_soon_threadsafe(lambda: self._loop.create_task(self._broadcast(msg)))
        except RuntimeError:
            pass

    # distinct world cities so coordless sources spread out on the map (not overlapping)
    _CITIES = [
        (52.370, 4.895), (41.008, 28.978), (51.507, -0.128), (40.713, -74.006),
        (35.676, 139.650), (48.857, 2.352), (25.205, 55.271), (1.352, 103.820),
        (34.052, -118.244), (55.756, 37.617), (-33.868, 151.209), (19.076, 72.878),
    ]

    def _ensure_coords(self) -> None:
        """Give coordless sources distinct city coords so they show on the map (item 3)."""
        for i, s in enumerate(self.db.list_sources()):
            if s.map_x is None or s.map_y is None:
                lat, lng = self._CITIES[i % len(self._CITIES)]
                try:
                    self.db.set_source_position(s.id, lat, lng)
                except Exception:  # noqa: BLE001
                    pass

    def add_source(self, name: str, url: str) -> int:
        """Add a camera AND immediately give it map coordinates, so a freshly added camera
        shows up on the map right away instead of being invisible until manually placed.
        The operator can then drag it to its real spot."""
        sid = self.db.add_source(name, url)
        self._ensure_coords()   # assigns spread coords to the new (coordless) source
        self._emit({"t": "cameras", "d": self.sources_payload()})
        return sid

    def _source_name(self, sid: int | None) -> str:
        if sid is None:
            return "—"
        for s in self.db.list_sources():
            if s.id == sid:
                return s.name
        return str(sid)

    # ---- sources / status --------------------------------------------
    def sources_payload(self) -> list[dict[str, Any]]:
        from .ytstream import is_stream_url
        out = []
        for s in self.db.list_sources():
            item: dict[str, Any] = {
                "id": str(s.id), "name": s.name, "url": s.url,
                "health": "online" if s.id == self._source_id else "offline",
                "coords": [s.map_x, s.map_y] if s.map_x is not None and s.map_y is not None else None,
                "fps": 0.0,
            }
            if is_stream_url(s.url):  # download progress for looped YouTube sources
                item["download"] = self.media.state(s.url)
            out.append(item)
        return out

    def rec_state(self) -> dict[str, Any]:
        if self._recorder is None:
            return {"rec": "off", "recActive": False}
        return {"rec": self._recorder.current_mode(), "recActive": self._recorder.is_recording()}

    def set_conn(self, state: str) -> None:
        self._conn = state
        self._emit({"t": "conn", "d": state})

    # ---- stream lifecycle --------------------------------------------
    def connect(self, source_id: int) -> None:
        with self._lock:
            # Already streaming this source with a healthy reader → no reconnect churn.
            if (source_id == self._source_id and self._reader is not None
                    and self._reader.is_alive() and self._conn == "online"):
                return
            self.disconnect()
            source = next((s for s in self.db.list_sources() if s.id == source_id), None)
            if source is None:
                log.warning("connect: unknown source %s", source_id)
                return
            self.set_conn("connecting")
            self._source_id = source_id
            self.db.touch_source(source_id)

            self.zones.set_zones(self.db.list_zones(source_id))
            self.trajectory.reset(); self.pose.reset(); self.objects.reset(); self.ooi.clear()
            self.speed.reset(); self.threat.reset(); self._last_threat_synth.clear()
            self.live_make.reset()
            self.ego.reset(); self._cam_moving = False
            self.alert_engine.reset(); self.alert_engine.set_rules(self.db.list_alert_rules())

            self._buffer = FrameBuffer(maxsize=int(self.config.get("camera.buffer_size", 5)))
            self._health = HealthMonitor(freeze_timeout=float(self.config.get("camera.freeze_timeout", 10.0)))
            self._plugins = PluginManager()
            self._plugins.register(MotionDetector(self.config))
            self._load_yolo(self._plugins)

            self._recorder = Recorder(self.config, self.db, on_status=lambda _s: self._push_system())
            self._recorder.source_id = source_id

            self._health.reset(time.time())
            self._worker = AnalysisWorker(
                self._buffer, self._plugins, self._health, self.bus, self.config,
                on_result=self._on_result, snapshots=self.snapshots, recorder=self._recorder,
                zones=self.zones, trajectory=self.trajectory, pose=self.pose, objects=self.objects,
            )
            self._worker.source_id = source_id
            self._worker.start()

            from .ytstream import is_stream_url
            if is_stream_url(source.url):
                # YouTube → download once and play the local file on a loop (never expires).
                local = self.media.local_path(source.url)
                if local is None:
                    st = self.media.state(source.url)
                    if st.get("status") == "failed":
                        self.set_conn("offline")
                        self._emit({"t": "alert", "d": {
                            "ts": time.time() * 1000, "severity": "warning", "type": "YOUTUBE BLOCKED",
                            "summary": "YouTube download failed — add config/youtube_cookies.txt (see README)",
                            "cam": source.name, "ack": False, "snapshot": None, "clip": None,
                        }})
                        return
                    # still downloading: keep 'connecting' and start playback when ready
                    self._emit({"t": "cameras", "d": self.sources_payload()})
                    self._start_media_waiter(source_id, source.url, source.name)
                    return
                self._reader = FileLoopReader(str(local), self._buffer, on_status=self._on_status)
            elif source.url.lower().startswith(("rtsp://", "rtmp://")):
                from .rtsp import RtspReader
                self._reader = RtspReader(source.url, self._buffer, on_status=self._on_status)
            else:
                self._reader = StreamReader(
                    source.url, self._buffer, on_status=self._on_status,
                    connect_timeout=float(self.config.get("camera.connect_timeout", 10.0)),
                    read_timeout=float(self.config.get("camera.read_timeout", 10.0)),
                    reconnect_min_delay=float(self.config.get("camera.reconnect_min_delay", 1.0)),
                    reconnect_max_delay=float(self.config.get("camera.reconnect_max_delay", 60.0)),
                )
            self._reader.start()
            self._emit({"t": "cameras", "d": self.sources_payload()})

    def _start_media_waiter(self, source_id: int, url: str, name: str) -> None:
        """Wait for a downloading YouTube source, then start looping playback — but only
        while it is still the active camera (the operator may switch away mid-download)."""
        def wait_and_start() -> None:
            for _ in range(1200):  # up to ~20 min
                if self._source_id != source_id:
                    return
                local = self.media.local_path(url)
                if local is not None:
                    with self._lock:
                        if self._source_id != source_id or self._reader is not None:
                            return
                        self._reader = FileLoopReader(
                            str(local), self._buffer, on_status=self._on_status)
                        self._reader.start()
                    self._emit({"t": "cameras", "d": self.sources_payload()})
                    return
                if self.media.state(url).get("status") == "failed":
                    if self._source_id == source_id:
                        self.set_conn("offline")
                    return
                time.sleep(1.0)
        threading.Thread(target=wait_and_start, name="MediaWaiter", daemon=True).start()

    def disconnect(self) -> None:
        with self._lock:
            if self._reader is not None:
                self._reader.stop(); self._reader.join(timeout=5); self._reader = None
            if self._worker is not None:
                self._worker.stop(); self._worker.join(timeout=5); self._worker = None
            if self._recorder is not None:
                self._recorder.request_close(); self._recorder = None
            if self._buffer is not None:
                self._buffer.clear()
            self._source_id = None
            self._latest_jpeg = None
            self._latest_img = None
            self.set_conn("offline")

    def _load_yolo(self, plugins: PluginManager) -> None:
        try:
            from ai.model_manager import ModelManager
            from ai.yolo import YoloBackend, create_yolo_detectors
            mm = ModelManager(Path("models"))
            model_path = mm.ensure_model(str(self.config.get("detectors.yolo.model", "yolo11s.pt")))
            backend = YoloBackend(
                model_path, mm.select_device(),
                confidence=float(self.config.get("detectors.yolo.confidence", 0.35)),
                imgsz=int(self.config.get("detectors.yolo.imgsz", 960)),
                frame_interval=int(self.config.get("detectors.yolo.frame_interval", 2)),
                slice_grid=int(self.config.get("detectors.yolo.slice", 0)),
                slice_overlap=float(self.config.get("detectors.yolo.slice_overlap", 0.2)),
                person_confidence=float(self.config.get("detectors.yolo.person_confidence", 0.18)),
            )
            for det in create_yolo_detectors(self.config, backend):
                plugins.register(det)
            self._yolo = backend  # handle for one-shot 'look closer' inference
            log.info("YOLO detectors online")
            self._start_roster_harvester(mm)
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            log.warning("YOLO unavailable, motion-only: %s", exc)

    def _start_roster_harvester(self, mm: Any = None) -> None:
        """Start the background thread that fills the roster from ALL cameras. Uses its own
        (lightweight) detector so it never races the live analysis YOLO. Runs from boot — it
        does NOT need an actively-analysed camera; passive cameras are scanned via their warm
        thumbnail and a periodic full-res grab."""
        with self._roster_boot_lock:
            if self._roster_harvester is not None or not bool(self.config.get("roster.enabled", True)):
                return
            try:
                from ai.model_manager import ModelManager
                from ai.yolo import YoloBackend
                from .roster import RosterHarvester
                if mm is None:
                    mm = ModelManager(Path("models"))
                det_path = mm.ensure_model(str(self.config.get("roster.detector", "yolo11n.pt")))
                det = YoloBackend(
                    det_path, mm.select_device(),
                    confidence=float(self.config.get("roster.confidence", 0.3)),
                    imgsz=int(self.config.get("roster.imgsz", 960)),
                    person_confidence=float(self.config.get("detectors.yolo.person_confidence", 0.18)),
                )
                self._roster_det = det
                conf = float(self.config.get("roster.confidence", 0.3))
                self._roster_harvester = RosterHarvester(
                    self.roster,
                    sources_fn=self.db.list_sources,
                    frame_fn=self._roster_frame,
                    detect_fn=lambda f: det.detect_crop(f, conf=conf),
                    embed_fn=self._roster_embed,
                    cat_to_cls=_CATEGORY_CLS,
                    plate_fn=self._roster_plate,
                    attrs_fn=self._roster_attrs,
                    clip_fn=self._roster_clip if bool(self.config.get("roster.clips", True)) else None,
                    watch_hit_fn=self._roster_watch_hit,
                    watch_cooldown=float(self.config.get("roster.watch_cooldown", 45.0)),
                    interval=float(self.config.get("roster.interval", 4.0)),
                )
                self._roster_harvester.start()
                log.info("roster harvester online")
            except Exception:  # noqa: BLE001
                log.exception("roster harvester failed to start")

    def _roster_frame(self, source: Any) -> Any:
        """Frame the roster harvester should scan. The active camera gives its full-res
        analysed frame for free. A passive camera gets a one-shot FULL-RESOLUTION grab every
        `roster.fullres_interval` seconds (so distant subjects there are found too), and its
        cheap thumbnail in between — light on the system."""
        sid = getattr(source, "id", None)
        if sid == self._source_id and self._latest_img is not None:
            return self._latest_img
        now = time.time()
        interval = float(self.config.get("roster.fullres_interval", 25.0))
        if interval > 0 and now - self._roster_fullres_last.get(sid, 0.0) >= interval:
            self._roster_fullres_last[sid] = now
            frame = self._grab_fullres(source)
            if frame is not None and getattr(frame, "size", 0) > 0:
                return frame
        return self._source_frame(source)   # between grabs: the cheap warm thumbnail

    def _grab_fullres(self, source: Any) -> Any:
        """One-shot full-resolution frame from a passive source (open, read, close). For a
        looped local video the sample point advances each time so different moments — and so
        different subjects — get seen; a stream just yields its current live frame."""
        from .ytstream import is_stream_url
        url = getattr(source, "url", "") or ""
        is_file = True
        if is_stream_url(url):
            local = self.media.cached_path(url)
            if local is None:
                return None                 # not downloaded yet; thumbnail covers it
            target = str(local)
        elif url.lower().startswith(("rtsp://", "rtmp://")):
            target, is_file = url, False
        else:
            target = url
            is_file = not url.lower().startswith(("http://", "https://"))
        cap = cv2.VideoCapture(target, cv2.CAP_FFMPEG)
        try:
            if not cap.isOpened():
                return None
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:  # noqa: BLE001
                pass
            if is_file:
                fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
                nframes = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
                dur_ms = (nframes / fps) * 1000.0 if fps > 0 else 0.0
                step = 3000.0
                sid = getattr(source, "id", None)
                off = self._roster_seek.get(sid, 0.0)
                self._roster_seek[sid] = (off + step) % dur_ms if dur_ms > step else 0.0
                if off > 0.0:
                    cap.set(cv2.CAP_PROP_POS_MSEC, off)
            ok, img = cap.read()
            if not is_file:  # skip a couple to get past a possibly-stale first frame
                for _ in range(2):
                    ok2, img2 = cap.read()
                    if ok2 and img2 is not None:
                        ok, img = ok2, img2
            return img if (ok and img is not None) else None
        except Exception:  # noqa: BLE001
            return None
        finally:
            cap.release()

    def _grab_burst(self, source: Any, n: int) -> list:
        """Read up to n consecutive frames from a passive source (open, read a run, close) —
        the raw material for a short sighting clip."""
        from .ytstream import is_stream_url
        url = getattr(source, "url", "") or ""
        is_file = True
        if is_stream_url(url):
            local = self.media.cached_path(url)
            if local is None:
                return []
            target = str(local)
        elif url.lower().startswith(("rtsp://", "rtmp://")):
            target, is_file = url, False
        else:
            target = url
            is_file = not url.lower().startswith(("http://", "https://"))
        cap = cv2.VideoCapture(target, cv2.CAP_FFMPEG)
        frames: list = []
        try:
            if not cap.isOpened():
                return []
            try:
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:  # noqa: BLE001
                pass
            if is_file:
                off = self._roster_seek.get(getattr(source, "id", None), 0.0)
                if off > 0.0:
                    cap.set(cv2.CAP_PROP_POS_MSEC, off)
            else:
                for _ in range(2):
                    cap.read()          # skip a couple of possibly-stale frames
            for _ in range(int(n)):
                ok, img = cap.read()
                if not ok or img is None:
                    break
                frames.append(img)
        except Exception:  # noqa: BLE001
            return frames
        finally:
            cap.release()
        return frames

    def _roster_clip(self, source: Any, nbbox: tuple) -> str | None:
        """A short, browser-playable clip of a roster sighting: a burst from the camera cropped
        (with padding) to the subject. nbbox is normalized so it maps onto the burst's own
        resolution. Best-effort — returns None if the burst can't be grabbed."""
        frames = self._grab_burst(source, int(self.config.get("roster.clip_frames", 16)))
        if len(frames) < 4:
            return None
        x1n, y1n, x2n, y2n = nbbox
        crops = []
        for f in frames:
            h, w = f.shape[:2]
            bw, bh = (x2n - x1n) * w, (y2n - y1n) * h
            cx1 = max(0, int(x1n * w - bw * 0.4))
            cy1 = max(0, int(y1n * h - bh * 0.4))
            cx2 = min(w, int(x2n * w + bw * 0.4))
            cy2 = min(h, int(y2n * h + bh * 0.4))
            if cx2 - cx1 < 24 or cy2 - cy1 < 24:
                return None
            crop = f[cy1:cy2, cx1:cx2]
            if crop.shape[1] > 380:      # bound the clip size
                s = 380.0 / crop.shape[1]
                crop = cv2.resize(crop, (380, max(1, int(crop.shape[0] * s))))
            crops.append(crop)
        h0, w0 = crops[0].shape[:2]      # writer needs a constant frame size
        crops = [c if c.shape[:2] == (h0, w0) else cv2.resize(c, (w0, h0)) for c in crops]
        return self._encode_clip(crops, fps=float(self.config.get("roster.clip_fps", 10.0)))

    def _roster_watch_hit(self, entry: dict) -> None:
        """A watched (BOLO) subject was re-identified — raise a critical alert on the WS channel
        with its photo and the camera it turned up on."""
        cam = entry.get("cam") or "—"
        label = entry["id"] + (f" · {entry['plate']}" if entry.get("plate") else "")
        self._emit({"t": "alert", "d": {
            "ts": time.time() * 1000, "severity": "critical", "type": "WATCHLIST HIT",
            "summary": f"{label} re-identified on {cam}", "cam": cam, "ack": False,
            "snapshot": entry.get("snapshot"), "clip": entry.get("clip"),
            "reason": f"BOLO subject {entry['id']} seen again — {entry.get('obs', 0)} sightings total",
        }})

    @staticmethod
    def _supercut_title(cam: str, idx: int, total: int, w: int, h: int, n: int = 6) -> list:
        """A short title card announcing the next leg of a subject's journey."""
        frame = np.zeros((h, w, 3), np.uint8)
        cv2.putText(frame, f"LEG {idx}/{total}", (16, 34), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (120, 120, 120), 1, cv2.LINE_AA)
        cv2.putText(frame, str(cam or "-")[:22], (16, h // 2), cv2.FONT_HERSHEY_SIMPLEX,
                    0.95, (236, 236, 236), 2, cv2.LINE_AA)
        cv2.line(frame, (16, h // 2 + 14), (w - 16, h // 2 + 14), (227, 208, 56), 1)  # cyan accent
        return [frame.copy() for _ in range(n)]

    def build_supercut(self, det_id: str) -> str | None:
        """Stitch a subject's per-camera sighting clips into one chronological journey video,
        each leg introduced by a camera title card. Cached until a new leg is captured."""
        segs = self.roster.clip_paths(det_id)
        if not segs:
            return None
        cached = self._supercut_cache.get(det_id)
        if cached is not None and cached[0] == len(segs):
            return cached[1]
        w, h = 480, 270
        frames: list = []
        for i, s in enumerate(segs):
            frames += self._supercut_title(s["cam"], i + 1, len(segs), w, h)
            path = self._snap_dir / "clips" / str(s["clip"]).rsplit("/", 1)[-1]
            if not path.exists():
                continue
            cap = cv2.VideoCapture(str(path))
            try:
                while True:
                    ok, f = cap.read()
                    if not ok or f is None:
                        break
                    frames.append(f if f.shape[:2] == (h, w) else cv2.resize(f, (w, h)))
            finally:
                cap.release()
        if len(frames) < 4:
            return None
        url = self._encode_clip(frames, fps=float(self.config.get("roster.clip_fps", 10.0)))
        if url:
            self._supercut_cache[det_id] = (len(segs), url)
        return url

    def _roster_embed(self, crop: Any, cls: str) -> Any:
        """A ReID appearance embedding for a crop, for roster de-duplication. Serialized so
        the harvester thread doesn't race a concurrent visual search on the same encoder."""
        eng = self._ensure_match_engine()
        if eng is None:
            return None
        enc = eng.encoders.get(cls) or eng.encoders.get("object")
        if enc is None or not enc.available():
            return None
        with self._embed_lock:
            try:
                m = enc.encode([crop])
                return m[0] if getattr(m, "shape", (0,))[0] else None
            except Exception:  # noqa: BLE001
                return None

    def _roster_plate(self, crop: Any) -> str | None:
        from match.anpr.normalize import normalize_plate
        reads = sorted(self.plates.read(crop), key=lambda r: -r[1])
        if reads and reads[0][1] >= 0.5:
            p = normalize_plate(reads[0][0])
            return p or None
        return None

    def _roster_attrs(self, crop: Any, cls: str) -> dict:
        try:
            band = crop[: max(1, crop.shape[0] // 2)] if cls == "person" else crop
            col = dominant_color_name(band)
            attrs = {"upper_color": col} if col and col != "unknown" else {}
            if cls == "vehicle":
                hit = self.make.classify(crop)   # confidence-gated brand; None if unsure
                if hit:
                    attrs["make"] = hit[0]
            return attrs
        except Exception:  # noqa: BLE001
            return {}

    # ---- commands -----------------------------------------------------
    def record_toggle(self) -> None:
        if self._recorder is None:
            return
        new = "off" if self._recorder.current_mode() != "off" else str(self.config.get("recording.mode", "event"))
        self._recorder.set_mode(new)
        self._push_system()

    def ooi_register(self, name: str, bbox_norm: list[float]) -> None:
        """Register an arbitrary object (normalized bbox) for visual tracking."""
        img = self._latest_img
        if img is None or len(bbox_norm) != 4:
            return
        h, w = img.shape[:2]
        x, y, bw, bh = bbox_norm
        self.ooi.register(name, (int(x * w), int(y * h), int(bw * w), int(bh * h)), img)

    def snapshot(self) -> str | None:
        if self._latest_img is None:
            return None
        try:
            return str(self.snapshots.save(self._latest_img, prefix="manual"))
        except OSError as exc:
            log.warning("snapshot failed: %s", exc)
            return None

    def _push_system(self) -> None:
        self._emit({"t": "system", "d": {"rec": self.rec_state()["rec"], "recActive": self.rec_state()["recActive"]}})

    # ---- worker-thread callbacks -------------------------------------
    def _on_status(self, status: str) -> None:
        mapping = {"connecting": "connecting", "reconnecting": "reconnecting",
                   "connected": "online", "stopped": "offline"}
        self.set_conn(mapping.get(status, self._conn))

    @staticmethod
    def _appearance(img: Any, x1: int, y1: int, x2: int, y2: int, cls: str, frame_h: int) -> dict | None:
        """Lightweight per-detection appearance attrs (colour + height band) for the
        live stream — feeds the tracking panel and client-side re-identification."""
        x1, y1 = max(0, x1), max(0, y1)
        if x2 <= x1 or y2 <= y1:
            return None
        bh, bw = y2 - y1, x2 - x1
        ratio = bh / max(1, frame_h)
        height = "short" if ratio < 0.33 else ("medium" if ratio < 0.66 else "tall")
        attrs: dict[str, Any] = {"height": height}
        if bh >= 24 and bw >= 12:  # skip tiny/far crops where colour is unreliable
            crop = img[y1:y2, x1:x2]
            if cls == "person":  # upper body carries the discriminative clothing colour
                crop = crop[: max(1, crop.shape[0] // 2)]
            try:
                color = dominant_color_name(crop)
                if color and color != "bilinmiyor":
                    attrs["upper_color"] = color
            except Exception:  # noqa: BLE001
                pass
        return attrs

    def _on_result(self, r: AnalysisResult) -> None:
        img = r.frame.image
        self._latest_img = img
        h, w = img.shape[:2]
        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        if ok:
            self._latest_jpeg = buf.tobytes()
        # keep a rolling window of recent frames (downscaled) for incident clips
        self._clip_ring.append(cv2.resize(img, (640, max(1, int(h * 640 / w)))) if w > 640 else img.copy())

        now = time.time()
        # camera ego-motion: fit the per-frame flow model once, so each vehicle's speed below
        # can be measured relative to the ground (a moving/dashcam feed is compensated per-object).
        self._cam_moving = self.ego.update(img)
        # feed the rolling window for appearance search (active camera only, bounded size)
        if self._source_id is not None and bool(self.config.get("match.enabled", True)):
            mw = int(self.config.get("match.store_max_width", 960))
            stored = cv2.resize(img, (mw, max(1, int(h * mw / w)))) if w > mw else img.copy()
            self.rolling.add(self._source_id, stored, now)
        dets = []
        idx = 0
        weapon_box = None
        for group in r.detections.values():
            for d in group:
                x1, y1, x2, y2 = d.bbox
                cls = _CATEGORY_CLS.get(d.category, "object")
                weapon = d.category == "weapon"
                if weapon:
                    weapon_box = (x1, y1, x2, y2)
                # Only surface people / vehicles / animals (and weapons) in the live
                # overlay. Generic objects (bags, motion blobs, night lights) are NOT
                # auto-tracked — the operator tracks a specific object via OOI instead.
                if cls == "object" and not weapon:
                    continue
                # stable id when ByteTrack has a track_id; else unique-per-frame
                tid = str(d.track_id) if d.track_id is not None else f"x{idx}"
                det = {
                    "id": f"TK_{(self._source_id or 0):03d}.{tid}",
                    "cls": cls,
                    "bbox": [x1 / w, y1 / h, (x2 - x1) / w, (y2 - y1) / h],
                    "conf": float(d.confidence),
                    "severity": "critical" if weapon else "info",
                    "klass": "WEAPON" if weapon else _CLS_KLASS.get(cls, "TRACKED"),
                }
                attrs = self._appearance(img, int(x1), int(y1), int(x2), int(y2), cls, h)
                if attrs:
                    det["attrs"] = attrs
                # vehicles: surface the fine COCO subtype (car / truck / bus / motorcycle),
                # a rough km/h estimate, and — once ANPR agrees across frames — the voted plate
                if cls == "vehicle":
                    det["subtype"] = d.label
                    if d.track_id is not None:
                        cx1, cy1, cx2, cy2 = max(0, int(x1)), max(0, int(y1)), int(x2), int(y2)
                        if cx2 > cx1 and cy2 > cy1:
                            crop = img[cy1:cy2, cx1:cx2]
                            self.plates.offer(det["id"], crop, now)
                            self.live_make.offer(det["id"], crop, now)   # brand, off-thread
                        plate = self.plates.plate_for(det["id"])
                        if plate:
                            det["plate"] = plate[0]
                        make = self.live_make.make_for(det["id"])
                        if make:
                            det["make"] = make
                        # the camera's own flow AT THIS VEHICLE'S ground point — so a car keeping
                        # pace on a dashcam reads the camera's speed, not "stopped".
                        ego_delta = self.ego.flow_at((x1 + x2) / 2.0, y2)
                        # scale from apparent size: real height ÷ box height => metres-per-pixel
                        # at this vehicle's depth. Unreliable when the box is clipped top/bottom.
                        real_h = self._class_heights.get(d.label, self._default_height)
                        reliable = y1 > 3 and y2 < h - 3
                        kmh = self.speed.update(d.track_id, (x1, y1, x2, y2), now,
                                                ego_delta=ego_delta, scale_ref_m=real_h,
                                                scale_reliable=reliable, cam_moving=self._cam_moving)
                        if kmh is not None:
                            det["speed"] = round(kmh)
                dets.append(det)
                idx += 1
        vehicle_ids = {d["id"] for d in dets}
        self.plates.prune(vehicle_ids)
        self.live_make.prune(vehicle_ids)
        self.speed.prune(now)
        self._emit({"t": "detections", "d": dets})

        # weapon alert with a cropped image of the weapon itself (throttled)
        if weapon_box is not None and now - self._last_weapon > 12.0:
            self._last_weapon = now
            self._emit({"t": "alert", "d": {
                "ts": now * 1000, "severity": "critical", "type": "WEAPON DETECTED",
                "summary": "Potential weapon / dangerous object in view",
                "cam": self._source_name(self._source_id), "ack": False,
                "snapshot": self._alert_snapshot_crop(img, weapon_box), "clip": self._save_clip(),
                "mark": self._alert_mark(weapon_box, "weapon", "WEAPON"),
            }})

        ooi = self.ooi.update(img)  # object-of-interest visual tracking
        if ooi:
            self._emit({"t": "ooi", "d": ooi})
            # Alert when a tracked object disappears (picked up / hidden / carried off).
            for o in ooi:
                oid = o["id"]
                if o["lost"] and not self._ooi_lost.get(oid, False) and now - self._last_ooi_alert.get(oid, 0.0) > 15.0:
                    self._last_ooi_alert[oid] = now
                    self._emit({"t": "alert", "d": {
                        "ts": now * 1000, "severity": "warning", "type": "TRACKED OBJECT MOVED",
                        "summary": f"{o['name']} left view — possibly taken or hidden",
                        "cam": self._source_name(self._source_id), "ack": False,
                        "snapshot": self._alert_snapshot(img), "clip": self._save_clip(),
                        "mark": {"bbox": o["bbox"], "kind": "object", "label": str(o["name"]).upper()} if o.get("bbox") else None,
                    }})
                self._ooi_lost[oid] = o["lost"]

        # keypoint pose behaviours (hand-raise) — low rate, best-effort
        self._pose_ctr += 1
        if self._pose_ctr % 10 == 0:
            for beh in self.pose_kp.detect(img):
                self._emit({"t": "event", "d": {
                    "ts": now * 1000, "type": beh["behavior"], "label": beh["behavior"],
                    "conf": None, "cam": str(self._source_id or ""),
                }})
                if now - self._last_handraise > 8.0:
                    self._last_handraise = now
                    self._emit({"t": "alert", "d": {
                        "ts": now * 1000, "severity": "warning", "type": "HAND RAISE",
                        "summary": "Raised-hand gesture detected", "cam": self._source_name(self._source_id),
                        "ack": False, "snapshot": self._alert_snapshot(img), "clip": self._save_clip(),
                    }})

        if now - self._last_frame_push > 0.2:
            self._last_frame_push = now
            m = r.metrics
            self._emit({"t": "frame", "d": {
                "fps": round(r.fps, 1), "res": [w, h], "inferenceMs": round(r.inference_ms, 1),
                "brightness": round(getattr(m, "brightness", 0.0), 1), "motionPct": round(r.motion_percent, 1),
                "movingCam": bool(self._cam_moving),
            }})

    def _alert_snapshot(self, img: Any = None) -> str | None:
        """Save the current frame for an alert and return its URL — every alert
        gets an image whenever a frame is available."""
        frame = img if img is not None else self._latest_img
        if frame is None:
            return None
        try:
            p = self.snapshots.save(frame, prefix="alert")
            rel = str(Path(p).relative_to(self._snap_dir)).replace("\\", "/")
            return f"/snapshots/{rel}"
        except Exception:  # noqa: BLE001
            return None

    def _frame_dims(self) -> tuple[int, int]:
        """(w, h) of the latest analysed frame, or a sane default."""
        img = self._latest_img
        if img is None:
            return (1920, 1080)
        h, w = img.shape[:2]
        return (w, h)

    def _alert_mark(self, bbox_px: Any, kind: str, label: str,
                    zone_id: int | None = None) -> dict | None:
        """Build the incident marker for the annotated replay: a normalized bbox of the
        triggering object + (for zone events) the zone polygon, so the overlay can draw a
        Overseer-style target where the operator should look. Returns None if nothing to mark."""
        w, h = self._frame_dims()
        mark: dict[str, Any] = {}
        if bbox_px is not None:
            try:
                x1, y1, x2, y2 = (float(v) for v in bbox_px)
                mark["bbox"] = [max(0.0, x1 / w), max(0.0, y1 / h),
                                max(0.0, (x2 - x1) / w), max(0.0, (y2 - y1) / h)]
            except Exception:  # noqa: BLE001
                pass
        if zone_id is not None:
            try:
                for zv in self.zones.snapshot():
                    if zv.zone_id == zone_id and zv.polygon:
                        mark["zone"] = [[px / w, py / h] for (px, py) in zv.polygon]
                        break
            except Exception:  # noqa: BLE001
                pass
        if not mark:
            return None
        mark["kind"] = kind
        mark["label"] = (label or kind).upper()
        return mark

    def inspect(self, cx: float, cy: float) -> list[dict]:
        """'Look closer': crop around the clicked point, upscale + enhance, and run a
        low-confidence pass to surface objects the live detector missed."""
        img = self._latest_img
        yolo = self._yolo
        if img is None or yolo is None:
            return []
        h, w = img.shape[:2]
        rw, rh = int(w * 0.16), int(h * 0.16)
        px, py = int(cx * w), int(cy * h)
        x0, y0 = max(0, px - rw), max(0, py - rh)
        x1, y1 = min(w, px + rw), min(h, py + rh)
        crop = img[y0:y1, x0:x1]
        if crop.size == 0:
            return []
        up = cv2.resize(crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        from ai.yolo import _enhance_lowlight
        if float(up.mean()) < 90.0:
            up = _enhance_lowlight(up, float(up.mean()))
        out: list[dict] = []
        for idx, d in enumerate(yolo.detect_crop(up, conf=0.12)):
            bx1, by1, bx2, by2 = d.bbox  # 2x-upscaled crop coords → full frame
            fx1, fy1 = x0 + bx1 / 2.0, y0 + by1 / 2.0
            fx2, fy2 = x0 + bx2 / 2.0, y0 + by2 / 2.0
            cls = _CATEGORY_CLS.get(d.category, "object")
            out.append({
                "id": f"IN_{(self._source_id or 0):03d}.{idx}", "cls": cls,
                "bbox": [fx1 / w, fy1 / h, (fx2 - fx1) / w, (fy2 - fy1) / h],
                "conf": float(d.confidence), "severity": "info",
                "klass": "WEAPON" if d.category == "weapon" else _CLS_KLASS.get(cls, "TRACKED"),
            })
        return out

    def _encode_clip(self, frames: list, fps: float = 10.0) -> str | None:
        """Write frames to a browser-playable clip under snapshots/clips and return its URL."""
        path = encode_clip(frames, self._snap_dir / "clips", f"clip_{int(time.time() * 1000)}", fps)
        return f"/snapshots/clips/{path.name}" if path is not None else None

    def _save_clip(self) -> str | None:
        """The rolling window around an incident, as a short browser-playable clip."""
        frames = list(self._clip_ring)
        return self._encode_clip(frames, fps=10.0) if len(frames) >= 5 else None

    def _alert_snapshot_crop(self, img: Any, box: Any) -> str | None:
        """Save a padded crop around a detection (e.g. the weapon) as the alert image."""
        try:
            x1, y1, x2, y2 = (int(v) for v in box)
            bw, bh = x2 - x1, y2 - y1
            cx1, cy1 = max(0, int(x1 - bw * 0.35)), max(0, int(y1 - bh * 0.35))
            cx2, cy2 = min(img.shape[1], int(x2 + bw * 0.35)), min(img.shape[0], int(y2 + bh * 0.35))
            crop = img[cy1:cy2, cx1:cx2]
            if crop.size == 0:
                return self._alert_snapshot(img)
            p = self.snapshots.save(crop, prefix="weapon")
            rel = str(Path(p).relative_to(self._snap_dir)).replace("\\", "/")
            return f"/snapshots/{rel}"
        except Exception:  # noqa: BLE001
            return self._alert_snapshot(img)

    def _source_frame_by_id(self, sid: str) -> Any:
        src = next((s for s in self.db.list_sources() if str(s.id) == str(sid)), None)
        return self._source_frame(src) if src is not None else None

    def _source_frame(self, s: Any) -> Any:
        """Latest BGR frame for a source — the analysed frame if it's the active
        camera, else the warm thumbnail relay (decoded)."""
        if s.id == self._source_id and self._latest_img is not None:
            return self._latest_img
        jpeg = self.thumbs.get_jpeg(s.id, s.url)
        if jpeg:
            return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        return None

    def _ensure_match_engine(self):
        """Build the appearance-match engine once, lazily (it needs the YOLO detector).
        Specialized ReID/ANPR/seg models load if their weights are present under models/,
        else the deterministic baseline is used and reported in self.match_info."""
        if self.match_engine is not None:
            return self.match_engine
        # Prefer the live analysis detector; fall back to the roster harvester's own detector
        # so ReID embeddings (and thus roster de-duplication) work from boot, before any camera
        # is actively analysed.
        det_backend = self._yolo or self._roster_det
        if det_backend is None:
            return None
        from match.factory import build_engine
        conf = float(self.config.get("match.detect_conf", 0.25))

        def detect(frame: Any) -> Any:
            return (self._yolo or self._roster_det).detect_crop(frame, conf=conf)

        try:
            self.match_engine, self.match_info = build_engine(
                self.config, detect, models_dir="models", category_to_cls=_CATEGORY_CLS)
            log.info("match engine ready: %s", self.match_info)
        except Exception:  # noqa: BLE001 - degrade gracefully; search just returns nothing
            log.exception("match engine build failed")
            self.match_engine = None
        return self.match_engine

    def _infer_query_class(self, crop: Any) -> str:
        """Best-guess the class of an uploaded query crop by detecting inside it."""
        if self._yolo is None:
            return "object"
        try:
            dets = self._yolo.detect_crop(crop, conf=0.2)
        except Exception:  # noqa: BLE001
            return "object"
        best, best_conf = "object", -1.0
        for d in dets:
            if d.confidence > best_conf:
                best_conf = d.confidence
                best = _CATEGORY_CLS.get(d.category, "object")
        return best

    def _query_plate(self, crop: Any) -> str | None:
        """Read a plate off the query crop itself; a confident read seeds a definitive
        cross-camera plate match."""
        eng = self.match_engine
        if eng is None or eng.plate_reader is None:
            return None
        try:
            from match.anpr.normalize import normalize_plate
            reads = sorted(eng.plate_reader(crop) or [], key=lambda r: -r[1])
            for text, conf in reads:
                p = normalize_plate(text, eng.plate_fold)
                if p and conf >= 0.5:
                    return p
        except Exception:  # noqa: BLE001
            pass
        return None

    @staticmethod
    def _hit_to_dict(h: Any) -> dict:
        e = h.evidence
        return {
            "camId": str(h.source_id), "cam": h.source_name, "cls": h.cls,
            "score": h.score, "confidence": h.confidence, "margin": h.margin,
            "ambiguous": h.ambiguous, "plate": h.plate, "bbox": list(h.bbox_norm),
            "evidence": {
                "score": e.score, "margin": e.margin, "det_conf": e.det_conf,
                "mask_coverage": e.mask_coverage, "temporal_support": e.temporal_support,
                "model_id": e.model_id, "trust": e.trust, "plate": e.plate,
                "plate_conf": e.plate_conf, "plate_match": e.plate_match,
            },
        }

    def visual_match(self, entity_bgr: Any, kind: str | None = None,
                     thresh: float = 0.42) -> list[dict]:
        """Find a watchlist entity across cameras by real appearance identity.

        Detects same-class candidates in each source, encodes them with a ReID/embedding
        model (masked to the subject), and scores by cosine — stabilised over the active
        camera's last-N-seconds window. Vehicles also run ANPR: a plate matching the
        query's plate is definitive. Every hit carries evidence (score parts, confidence,
        margin, model) so the result explains itself; a near-tie is flagged, not asserted."""
        if entity_bgr is None or getattr(entity_bgr, "size", 0) == 0:
            return []
        eng = self._ensure_match_engine()
        if eng is None:
            return []
        cls = {"person": "person", "vehicle": "vehicle", "animal": "animal",
               "pet": "animal", "object": "object"}.get(kind or "", None)
        if cls is None:
            cls = self._infer_query_class(entity_bgr)
        plate = self._query_plate(entity_bgr) if cls == "vehicle" else None
        query = Query(cls=cls, crop=entity_bgr, plate=plate)

        now = time.time()
        sources: list[SourceFrames] = []
        for s in self.db.list_sources():
            window = self.rolling.window(s.id, now) if s.id == self._source_id else []
            if not window:
                f = self._source_frame(s)
                if f is not None and getattr(f, "size", 0) > 0:
                    window = [f]
            if window:
                sources.append(SourceFrames(s.id, s.name, window))

        res = eng.match(query, sources, accept_threshold=float(thresh))
        return [self._hit_to_dict(h) for h in res.hits]

    def find_across(self, det_id: str) -> list[dict]:
        """Find a roster subject across all cameras by appearance identity — its stored photo
        becomes the ReID query. Works for vehicles and people (vehicles also match on plate)."""
        got = self.roster.snapshot_bgr(det_id)
        if got is None:
            return []
        img, cls = got
        return self.visual_match(img, cls)

    def plate_match(self, plate: str) -> list[dict]:
        """Find a vehicle across live cameras whose plate matches the query. Runs ANPR on
        the vehicles in each source's current frame and compares (confusable-tolerant) to
        the query; returns the best hit per camera with a normalized plate + bbox."""
        if not plate or self._yolo is None or not self.plates.available():
            return []
        from match.anpr.normalize import normalize_plate, plate_similarity
        q = normalize_plate(plate, fold_confusable=True)  # tolerant match
        if not q:
            return []
        out: list[dict] = []
        for s in self.db.list_sources():
            frame = self._source_frame(s)
            if frame is None or getattr(frame, "size", 0) == 0:
                continue
            fh, fw = frame.shape[:2]
            best: tuple | None = None
            for d in self._yolo.detect_crop(frame, conf=0.25):
                if _CATEGORY_CLS.get(d.category, "object") != "vehicle":
                    continue
                x1, y1, x2, y2 = (int(v) for v in d.bbox)
                x1, y1, x2, y2 = max(0, x1), max(0, y1), min(fw, x2), min(fh, y2)
                if x2 <= x1 or y2 <= y1:
                    continue
                for text, _conf in self.plates.read(frame[y1:y2, x1:x2]):
                    sim = plate_similarity(q, text, fold_confusable=True)
                    if sim >= 0.7 and (best is None or sim > best[0]):
                        best = (sim, normalize_plate(text, fold_confusable=False), (x1, y1, x2, y2))
            if best:
                sim, pl, (x1, y1, x2, y2) = best
                out.append({"camId": str(s.id), "cam": s.name, "plate": pl,
                            "score": round(float(sim), 3),
                            "bbox": [x1 / fw, y1 / fh, (x2 - x1) / fw, (y2 - y1) / fh]})
        out.sort(key=lambda m: -m["score"])
        return out

    def _on_event(self, ev: Event) -> None:
        type_en = ev.type.name.replace("_", " ")
        self._emit({"t": "event", "d": {
            "ts": ev.timestamp * 1000, "type": type_en, "label": (ev.label or "").upper(),
            "conf": ev.confidence, "cam": str(ev.source_id or ""),
        }})
        threat = self.threat.observe(ev)   # standing threat for this camera after this event
        alert = self.alert_engine.evaluate(ev)
        if alert is not None:
            snap_url = self._alert_snapshot()
            try:
                self.db.add_alert(alert)
            except Exception:  # noqa: BLE001
                pass
            meta = ev.metadata or {}
            zid = meta.get("zone_id")
            mark = self._alert_mark(
                ev.bbox, _EVENT_KIND.get(ev.type.name, "person"),
                ev.label or type_en, zone_id=int(zid) if isinstance(zid, int) else None)
            # a live correlated threat raises this alert's severity and explains why
            severity = escalate(alert.severity, threat.level) if threat.combo else alert.severity
            reason = "; ".join(threat.reasons) if threat.combo else None
            self._emit({"t": "alert", "d": {
                "ts": alert.timestamp * 1000, "severity": severity,
                "type": type_en, "summary": f"{type_en} · {self._source_name(alert.source_id)}",
                "cam": self._source_name(alert.source_id), "ack": False,
                "snapshot": snap_url, "clip": self._save_clip(), "mark": mark,
                **({"reason": reason} if reason else {}),
            }})
        elif threat.combo and threat.level in ("high", "critical"):
            # a dangerous COMBINATION with no matching rule — surface it as its own threat,
            # rate-limited per camera so a persistent situation doesn't spam
            self._maybe_synth_threat(ev, threat, type_en)

    def _maybe_synth_threat(self, ev: Event, threat: Any, type_en: str) -> None:
        src = ev.source_id
        last = self._last_threat_synth.get(src)
        if last is not None and ev.timestamp - last < self._threat_synth_cooldown:
            return
        self._last_threat_synth[src] = ev.timestamp
        reason = "; ".join(threat.reasons)
        mark = self._alert_mark(ev.bbox, _EVENT_KIND.get(ev.type.name, "person"),
                                "THREAT")
        self._emit({"t": "alert", "d": {
            "ts": ev.timestamp * 1000, "severity": threat.severity,
            "type": "CORRELATED THREAT",
            "summary": f"{reason} · {self._source_name(src)}",
            "cam": self._source_name(src), "ack": False,
            "snapshot": self._alert_snapshot(), "clip": self._save_clip(), "mark": mark,
            "reason": f"Correlated signals: {', '.join(threat.signals)}",
        }})

    # ---- MJPEG sources -----------------------------------------------
    def latest_jpeg(self) -> bytes | None:
        return self._latest_jpeg

    def stream_frame(self, source_id: str) -> bytes | None:
        """POV frame for a specific camera. The active (analysed) camera returns its
        enhanced analysed JPEG; any other camera returns the persistent warm relay
        frame — one connection per camera, referenced everywhere, so switching in
        and out of feeds never drops to a blank while a reader re-establishes."""
        try:
            same = str(self._source_id) == str(source_id)
        except Exception:  # noqa: BLE001
            same = False
        if same and self._latest_jpeg is not None:
            return self._latest_jpeg
        src = next((s for s in self.db.list_sources() if str(s.id) == str(source_id)), None)
        if src is not None:
            j = self.thumb_jpeg(src.id)  # spins up / keeps the relay warm (YouTube -> local file)
            if j:
                return j
        return self._latest_jpeg

    def _prewarm_thumbs(self) -> None:
        """Spin up the preview relay for every source at startup so the map has
        thumbnails ready (and refreshes the persistent cache) without waiting."""
        try:
            for s in self.db.list_sources():
                self.thumb_jpeg(s.id)  # YouTube -> begins the one-time download + local preview
        except Exception:  # noqa: BLE001
            pass

    def thumb_jpeg(self, source_id: int) -> bytes | None:
        """Latest raw JPEG for a camera's lightweight preview (item 4). YouTube sources
        preview from their downloaded local file (which never expires); while a source is
        still downloading, its last-known frame is served rather than opening a live stream."""
        src = next((s for s in self.db.list_sources() if s.id == source_id), None)
        if src is None:
            return None
        from .ytstream import is_stream_url
        if is_stream_url(src.url):
            # Previews NEVER trigger a download (merely viewing the map must not start a
            # multi-GB fetch). The download happens on connect(); until the file is local,
            # show the video's YouTube poster so the camera has an image immediately.
            local = self.media.cached_path(src.url)
            if local is not None:
                return self.thumbs.get_jpeg(source_id, str(local))
            return self.media.poster(src.url) or self.thumbs.last(source_id)
        return self.thumbs.get_jpeg(source_id, src.url)

    def reap_thumbs(self) -> None:
        self.thumbs.reap()

    def shutdown(self) -> None:
        self.disconnect()
        if self._roster_harvester is not None:
            self._roster_harvester.stop()
        self.plates.stop()
        self.live_make.stop()
        self.thumbs.stop_all()
        try:
            self._unsub()
            self.event_recorder.close()
        except Exception:  # noqa: BLE001
            pass
        self.db.close()
