"""Headless orchestrator: replicates ui/app.py wiring without Qt, bridging the
existing pipeline (camera -> AnalysisWorker -> EventBus/DB) to async web transports.

Thread model (per backend integration reference):
  StreamReader (thread) -> FrameBuffer (drop-oldest) -> AnalysisWorker (thread).
  on_result & bus callbacks fire ON THE WORKER THREAD -> we hop to the asyncio loop
  via loop.call_soon_threadsafe.
"""
from __future__ import annotations

import base64
import json
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
from forensic.palette import dominant_color_name, dominant_color_name_conf, skin_fraction
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
from . import spatial, suggestions
from .clipenc import encode_clip
from .coverage import CoverageField
from .grain import GrainEngine
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
        # On a fresh install, seed the public demo cameras so the map isn't empty.
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
        # Plate watchlist (BOLO for plates): reading a watched plate on any camera fires an alert
        self._plate_watch: set[str] = set()
        self._plate_watch_last: dict[str, float] = {}
        self._plate_watch_cd = float(self.config.get("match.anpr.watch_cooldown", 30.0))
        from .relationships import RelationshipGraph
        self.relationships = RelationshipGraph()   # co-occurrence graph between roster subjects
        from .cameradna import CameraProfiles
        self.cam_profiles = CameraProfiles()       # per-camera DNA + reputation from observations
        from trajectory.intent import IntentEstimator
        self.intent = IntentEstimator()            # probabilistic behavioural intent per track
        # Monocular depth for the spatial 3D scene view (Feature 4). Lazy — the model loads on
        # first request only, so a system that never opens the spatial view pays nothing.
        from .depth import DepthEstimator
        self._depth = DepthEstimator(
            model_name=str(self.config.get("spatial.model",
                                           "depth-anything/Depth-Anything-V2-Large-hf")),
            input_size=self.config.get("spatial.depth_res"))
        # Session roster: an anonymous, deduped registry of people + vehicles seen, with a
        # photo each (and plates for vehicles). Background cutouts use the YOLO-seg model.
        from match.seg_backend import YoloSegBackend
        from .roster import SessionRoster
        _seg_name = str(self.config.get("match.models.seg", "") or "")
        _roster_seg = YoloSegBackend(Path("models") / _seg_name) if _seg_name else None
        # Long-term cross-session identity: recognize subjects across days + build repeat-visitor
        # dossiers (features 5/6). Persists into the shared SQLite db; degrades to off on any error.
        self.subject_store = None
        if bool(self.config.get("roster.persist", True)):
            try:
                from .identity_store import SubjectStore
                self.subject_store = SubjectStore(
                    self.db,
                    appearance_threshold=float(self.config.get("roster.persist_threshold", 0.74)))
            except Exception:  # noqa: BLE001
                self.subject_store = None
        self.roster = SessionRoster(
            self.snapshots, self._snap_dir, _roster_seg,
            dedup_threshold=float(self.config.get("roster.dedup_threshold", 0.82)),
            auto_merge=bool(self.config.get("roster.auto_merge.enabled", True)),
            auto_merge_threshold=float(self.config.get("roster.auto_merge.threshold", 0.85)),
            subject_store=self.subject_store)
        # Vehicle make/brand classifier for roster profiles (CPU, off the GPU hot path).
        # Quiet unless its weights are present under models/ (uv run -m match.tools.export_models
        # --only carbrand). Confidence-gated so it never asserts a confident-but-wrong brand.
        # Thresholds raised: the make classifier can be confidently WRONG on smaller/blurrier crops
        # (e.g. Renault read as Daewoo), so only surface a brand when the crop is large and the guess
        # is strong and clearly ahead of the runner-up. Better to show no make than a wrong one.
        self.make = MakeClassifier(
            Path("models") / str(self.config.get("vehicle.make.model", "vehicle_make.torchscript")),
            min_conf=float(self.config.get("vehicle.make.min_conf", 0.55)),
            min_margin=float(self.config.get("vehicle.make.min_margin", 0.20)),
            min_area=int(self.config.get("vehicle.make.min_area", 9000)))
        # Background brand reader for the live tracking card (classification is CPU-bound, so
        # it runs off-thread and the card reads the cached brand — never stalls the analysis).
        from .live_make import LiveMakeReader
        self.live_make = LiveMakeReader(
            self.make, interval=float(self.config.get("vehicle.make.live_interval", 4.0)))
        # Zero-shot CLIP body-type (sedan / hatchback / SUV / ...) refines the coarse COCO class.
        # Same off-thread reader plumbing as the brand: gated, cached per track, quiet if unavailable.
        from vehicle.bodytype import BodyTypeClassifier
        self.bodytype = BodyTypeClassifier(
            min_conf=float(self.config.get("vehicle.bodytype.min_conf", 0.35)),
            min_margin=float(self.config.get("vehicle.bodytype.min_margin", 0.15)),
            min_area=int(self.config.get("vehicle.bodytype.min_area", 9000)))
        self.live_bodytype = LiveMakeReader(
            self.bodytype, interval=float(self.config.get("vehicle.bodytype.live_interval", 5.0)))
        self._embed_lock = threading.Lock()   # serialize ReID encoder use (harvester vs search)
        self._roster_harvester = None
        self._roster_det = None
        self._motion_det = None
        # Operator's per-class DETECTION toggles (person / vehicle / animal / weapon /
        # motion / track). Persisted in the settings table so a disabled class stays off
        # (and off the processing budget) across restarts. `track` is display-only: the
        # tracker underpins ReID, so it is never actually torn down backend-side.
        self._detection_filters = self._load_detection_filters()
        self._roster_boot_lock = threading.Lock()  # guard boot-vs-connect harvester start race
        self._supercut_cache: dict[str, tuple[int, str]] = {}   # det_id -> (n legs, url)
        self._roster_fullres_last: dict[int, float] = {}  # per-source last full-res grab time
        self._roster_seek: dict[int, float] = {}          # rotating sample point in looped files
        self._prewarm_thumbs()
        self.ooi = OOIManager()   # object-of-interest visual tracker
        self.pose_kp = PoseKP()   # keypoint pose behaviours (hand-raise) + gait skeletons (feature 5)
        self._pose_ctr = 0
        self._facing: dict[str, tuple[float, float]] = {}   # det_id -> (heading_deg, ts), from the pose pass
        self._appearance_cache: dict[str, tuple[dict, int]] = {}   # det_id -> (attrs, pose_ctr); colour is stable per subject
        self.gait_tracker = None
        if bool(self.config.get("gait.enabled", True)) and self.subject_store is not None:
            try:
                from .gait import GaitTracker
                self.gait_tracker = GaitTracker()
            except Exception:  # noqa: BLE001
                self.gait_tracker = None
        self._gait_recorded: dict[str, float] = {}   # track key -> last gait persist ts (throttle)
        # learned super-resolution for photo reconstruction (feature 7); lazy, downloads once, degrades
        # to the classical enhancement if torch/weights are unavailable.
        self._sr = None
        if bool(self.config.get("reconstruct.super_resolution", True)):
            try:
                from .sr_model import SuperResolver
                self._sr = SuperResolver(models_dir="models")
            except Exception:  # noqa: BLE001
                self._sr = None
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
        self._latest_raw_jpeg: bytes | None = None   # full-rate display frame (decoupled from analysis)
        self._latest_raw: Any = None                 # newest RAW capture frame (ref only, set on the capture thread)
        self._raw_seq = 0                            # bumped on every capture; the encoder thread watches it
        self._enc_seq = -1
        self._disp_run = False                       # dedicated display-encoder thread: steady 30 fps, off the hot paths
        self._disp_thread: threading.Thread | None = None
        self._last_anal_enc = 0.0                    # throttle the analysed-frame JPEG (a warm fallback now)
        self._last_det_push = 0.0                    # throttle the detections WS emit so it can't starve /stream
        self._bgp_ctr = 0                            # background-plate EMA runs every 3rd frame
        self._bg_plate: Any = None       # running background estimate (EMA) of the active camera
        self._bg_plate_sid: Any = None   # which camera the plate belongs to
        self._bg_plate_n = 0             # frames accumulated (plate is only trusted once warmed up)
        self._last_frame_push = 0.0

        # FOG OF WAR — the observability field. `observe` is two dict updates per track on the
        # analysis worker; the expensive geometry only runs when the operator asks for it, and
        # reuses the depth grid the spatial view already produced rather than inferring again.
        self.coverage = CoverageField(self.db, self.config)
        self._depth_cache: tuple[str, Any, float] | None = None   # (sid, disp01, ts)
        self._last_loss_check = 0.0

        # GRAIN — the behavioural grain of the place. Movement only, never appearance. Wired to
        # FOG OF WAR so a track that ends inside a known shadow is not scored as a
        # disappearance: without that link both features generate noise.
        self.grain = GrainEngine(self.db, self.config, occluded=self._grain_occluded)

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
        # Safety net: an incident alert must never reach the operator empty. If it carries
        # neither a still nor a clip, backfill from the current frame / rolling buffer so the
        # alert card (and any case opened from it) always has footage.
        if msg.get("t") == "alert" and isinstance(msg.get("d"), dict):
            d = msg["d"]
            if not d.get("snapshot") and not d.get("clip"):
                snap = self._alert_snapshot()
                if snap:
                    d["snapshot"] = snap
                clip = self._save_clip()
                if clip:
                    d["clip"] = clip
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
            # Online = the active analysis source, OR a camera whose preview relay is currently
            # producing frames (really reachable) — not merely "is this the active camera".
            live = (s.id == self._source_id and self._conn == "online") or self.thumbs.is_live(s.id)
            item: dict[str, Any] = {
                "id": str(s.id), "name": s.name, "url": s.url,
                "health": "online" if live else "offline",
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
            self.live_make.reset(); self.live_bodytype.reset()
            self.ego.reset(); self._cam_moving = False; self.intent.reset()
            self.alert_engine.reset(); self.alert_engine.set_rules(self.db.list_alert_rules())

            self._buffer = FrameBuffer(maxsize=int(self.config.get("camera.buffer_size", 5)))
            self._latest_raw_jpeg = None; self._latest_raw = None
            self._buffer.on_put = self._tap_frame   # full-rate display, independent of the analysis loop
            self._start_display_encoder()
            # Warm the pose model off-thread so the first pose pass (facing / Social X-ray / gait /
            # hand-raise) does not stall the analysis worker for seconds on its lazy first load.
            threading.Thread(target=self.pose_kp.warmup, name="PoseWarm", daemon=True).start()
            self._health = HealthMonitor(freeze_timeout=float(self.config.get("camera.freeze_timeout", 10.0)))
            self._plugins = PluginManager()
            self._motion_det = MotionDetector(self.config)
            self._plugins.register(self._motion_det)
            self._load_yolo(self._plugins)
            # apply the persisted class toggles to the freshly built detectors
            self._apply_detection_filters()

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
            self._latest_raw_jpeg = None
            self.set_conn("offline")

    # ---- detection class filters (operator MODULES rail) ---------------------------
    _DETECTION_DEFAULTS = {"person": True, "vehicle": True, "animal": True,
                           "weapon": True, "motion": True, "track": True}
    _FILTER_CATEGORIES = {"person", "vehicle", "animal", "weapon"}  # keys that gate YOLO

    def _load_detection_filters(self) -> dict:
        saved: dict = {}
        try:
            raw = self.db.get_setting("detection.filters")
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict):
                    saved = {k: bool(v) for k, v in data.items()}
        except Exception:  # noqa: BLE001 - never let a bad setting block boot
            saved = {}
        return {**self._DETECTION_DEFAULTS, **saved}

    def get_detection_filters(self) -> dict:
        return dict(self._detection_filters)

    def set_detection_filters(self, filters: dict) -> dict:
        """Merge the operator's toggles, persist them, and apply live to every detector so a
        disabled class immediately stops costing us inference + all downstream work."""
        for k, v in (filters or {}).items():
            if k in self._DETECTION_DEFAULTS:
                self._detection_filters[k] = bool(v)
        try:
            self.db.set_setting("detection.filters", json.dumps(self._detection_filters))
        except Exception:  # noqa: BLE001
            pass
        self._apply_detection_filters()
        return dict(self._detection_filters)

    def _apply_detection_filters(self) -> None:
        """Push the current filters onto whatever detectors exist right now: the live YOLO,
        the roster harvester's YOLO, and the motion plugin. Safe before any exist (no-op)."""
        cats = {c for c in self._FILTER_CATEGORIES if self._detection_filters.get(c, True)}
        for yb in (self._yolo, self._roster_det):
            if yb is not None:
                try:
                    yb.set_categories(cats)
                except Exception:  # noqa: BLE001
                    pass
        if self._motion_det is not None:
            self._motion_det.enabled = bool(self._detection_filters.get("motion", True))

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
                    plate_hit_fn=self._roster_plate_hit,
                    relate_fn=self.relationships.observe_together,
                    profile_fn=self._roster_profile_frame,
                    watch_cooldown=float(self.config.get("roster.watch_cooldown", 45.0)),
                    interval=float(self.config.get("roster.interval", 4.0)),
                    pov_active_fn=lambda: self._source_id is not None,   # back off while viewing live
                    active_id_fn=lambda: self._source_id,   # while focused, only harvest THAT camera
                )
                self._apply_detection_filters()  # gate the harvester's YOLO to enabled classes too
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

    def _live_journey(self, det_id: str) -> str | None:
        """Fallback journey when a subject has no stored sighting clips yet: grab a fresh short
        clip from each camera on its trail (most recent first, capped) so Play Journey always has
        moving footage of where the subject was seen — never a dead button."""
        e = self.roster.get(det_id)
        if e is None:
            return None
        trail = e.get("trail") or []
        cams = [t["cam"] for t in sorted(trail, key=lambda t: -float(t.get("last", 0))) if t.get("cam")][:3]
        if not cams and e.get("cam"):
            cams = [e["cam"]]
        w, h = 480, 270
        frames: list = []
        for i, cam in enumerate(cams):
            src = next((s for s in self.db.list_sources() if s.name == cam), None)
            if src is None:
                continue
            try:
                burst = self._grab_burst(src, int(self.config.get("roster.clip_frames", 16)))
            except Exception:  # noqa: BLE001
                burst = []
            if not burst:
                continue
            frames += self._supercut_title(cam, i + 1, len(cams), w, h)
            for f in burst:
                frames.append(f if f.shape[:2] == (h, w) else cv2.resize(f, (w, h)))
        if len(frames) < 4:
            return None
        return self._encode_clip(frames, fps=float(self.config.get("roster.clip_fps", 10.0)))

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
            return self._live_journey(det_id)   # no stored legs yet -> capture fresh footage now
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

    # ---- plate watchlist ----------------------------------------------
    def watch_plate(self, plate: str, on: bool = True) -> list[str]:
        from match.anpr.normalize import normalize_plate
        p = normalize_plate(plate)
        if p:
            if on:
                self._plate_watch.add(p)
            else:
                self._plate_watch.discard(p)
                self._plate_watch_last.pop(p, None)
        return self.list_watched_plates()

    def list_watched_plates(self) -> list[str]:
        return sorted(self._plate_watch)

    def _check_plate_watch(self, plate: str | None, cam: str | None) -> None:
        """If a just-read plate is on the watchlist, raise an alert (cooldown-gated per plate)."""
        if not plate or not self._plate_watch:
            return
        from match.anpr.normalize import normalize_plate
        p = normalize_plate(plate)
        if not p or p not in self._plate_watch:
            return
        now = time.time()
        if now - self._plate_watch_last.get(p, 0.0) < self._plate_watch_cd:
            return
        self._plate_watch_last[p] = now
        self._emit({"t": "alert", "d": {
            "ts": now * 1000, "severity": "critical", "type": "PLATE WATCHLIST HIT",
            "summary": f"Plate {p} read on {cam or '—'}", "cam": cam or "", "ack": False,
            "snapshot": self._alert_snapshot(), "reason": f"Watched plate {p} detected",
        }})

    def _roster_plate_hit(self, plate: str, cam: str | None) -> None:
        self._check_plate_watch(plate, cam)

    def _roster_profile_frame(self, sid: object, brightness: float, dets: list,
                              points: list | None = None) -> None:
        """Feed a passively-scanned camera's frame into its DNA/reputation profile, so EVERY
        camera builds a fingerprint (crowd / quiet / night / low-light / pedestrian-vs-vehicle)
        AND a foot-point density grid for auto zone suggestions, not only the one being actively
        analysed. Motion/fps aren't measurable in the sweep, so neutral values are passed and the
        tags come from brightness + the detection mix."""
        if sid is None:
            return
        self.cam_profiles.observe_frame(sid, brightness=brightness, motion=0.0, fps=15.0,
                                        dets=dets, points=points)

    # ---- investigation cases -----------------------------------------
    def open_case_from_alert(self, alert: dict) -> int:
        """Create an investigation case seeded with an alert as its incident; returns its id.
        Guarantees footage: if the alert didn't freeze an incident clip at fire time, capture a
        short clip from the incident's own camera now, so a case is never footage-less."""
        typ = str(alert.get("type", "INCIDENT"))
        cam = str(alert.get("cam", "") or "")
        sev = str(alert.get("severity", "info"))
        ts = float(alert.get("ts", time.time() * 1000)) / 1000.0
        threat = {"critical": "high", "warning": "medium"}.get(sev, "low")
        name = (f"{typ} · {cam}".strip(" ·")) or typ
        clip = alert.get("clip")
        snapshot = alert.get("snapshot")
        if not clip:
            clip, snap2 = self._capture_case_footage(cam)
            snapshot = snapshot or snap2
        cid = self.db.add_case(name, threat_level=threat)
        self.db.add_case_event(cid, ts, "alert", event_type=typ, cam=cam, severity=sev,
                               summary=str(alert.get("summary", "")),
                               snapshot=snapshot, clip=clip)
        return cid

    def _capture_case_footage(self, cam: str) -> tuple[str | None, str | None]:
        """Grab a short clip (and a mid-frame still) from a camera by name, for a case that has
        no frozen incident clip. Best-effort — returns (clip_url|None, snapshot_url|None)."""
        src = next((s for s in self.db.list_sources() if s.name == cam), None)
        if src is None:
            return None, None
        try:
            frames = self._grab_burst(src, int(self.config.get("cases.clip_frames", 16)))
        except Exception:  # noqa: BLE001
            frames = []
        if len(frames) < 3:
            return None, None
        clip = self._encode_clip(frames, fps=float(self.config.get("roster.clip_fps", 10.0)))
        snap = None
        try:
            mid = frames[len(frames) // 2]
            p = self.snapshots.save(mid, prefix="case")
            snap = f"/snapshots/{Path(p).relative_to(self._snap_dir).as_posix()}"
        except Exception:  # noqa: BLE001
            pass
        return clip, snap

    def case_detail(self, case_id: int) -> dict | None:
        """A case as an investigation: its own incident events plus the surrounding scene events
        from the log in a window around the incident, ordered into a timeline, with a gated AI
        summary. This is what the investigation workspace renders."""
        case = self.db.get_case(case_id)
        if case is None:
            return None
        cevents = self.db.list_case_events(case_id)
        anchor = min((e.ts for e in cevents), default=case.created_at)
        pre = float(self.config.get("cases.window_before", 90.0))
        post = float(self.config.get("cases.window_after", 120.0))
        names = {s.id: s.name for s in self.db.list_sources()}
        timeline: list[dict] = []
        for e in cevents:
            timeline.append({"ts": e.ts * 1000, "kind": e.kind, "type": e.event_type, "cam": e.cam,
                             "severity": e.severity, "summary": e.summary,
                             "snapshot": e.snapshot, "clip": e.clip})
        try:
            for ev in self.db.search_events(anchor - pre, anchor + post, limit=150):
                snap = ev.snapshot_path if (ev.snapshot_path and "/snapshots/" in ev.snapshot_path) else None
                timeline.append({"ts": ev.timestamp * 1000, "kind": "event", "type": ev.type,
                                 "cam": names.get(ev.source_id, str(ev.source_id or "")),
                                 "severity": "info", "summary": ev.label or ev.type,
                                 "snapshot": snap, "clip": None})
        except Exception:  # noqa: BLE001
            pass
        timeline.sort(key=lambda x: x["ts"])
        ai_summary = None
        try:
            evs = [{"type": t["type"], "cam": t["cam"], "label": t.get("summary", "")}
                   for t in timeline if t["type"]][:40]
            ai_summary = self.ai.summarize(evs) if evs else None
        except Exception:  # noqa: BLE001
            ai_summary = None
        cams = sorted({t["cam"] for t in timeline if t["cam"]})
        incident_cam = next((e.cam for e in cevents if e.kind == "alert" and e.cam),
                            cevents[0].cam if cevents else None)
        subjects = self._case_scene_subjects(incident_cam, (anchor - pre) * 1000,
                                             (anchor + post) * 1000)
        return {"id": case.id, "name": case.name, "threat": case.threat_level, "notes": case.notes,
                "status": case.status, "created": case.created_at * 1000,
                "cameras": cams, "events": timeline, "subjects": subjects, "aiSummary": ai_summary}

    def _case_scene_subjects(self, cam: str | None, start_ms: float, end_ms: float,
                             limit: int = 8) -> list[dict]:
        """Roster subjects present at the incident camera during the case window, each with their
        strongest known associates — the 'who was at the scene, and who they're linked to' panel.
        Bridges the disjoint identity spaces by matching the roster trail (camera + time) to the
        incident, since alerts themselves carry no subject id."""
        if not cam:
            return []
        out: list[dict] = []
        for e in self.roster.list():
            legs = [t for t in e.get("trail", [])
                    if t.get("cam") == cam and t.get("last", 0) >= start_ms
                    and t.get("first", 0) <= end_ms]
            if not legs:
                continue
            out.append({
                "id": e["id"], "cls": e["cls"], "snapshot": e.get("snapshot"),
                "plate": e.get("plate"), "seen": sum(int(t.get("count", 0)) for t in legs),
                "associates": self.entity_relationships(e["id"], limit=3),
            })
        out.sort(key=lambda s: -s["seen"])
        return out[:limit]

    # ---- relationships -----------------------------------------------
    def entity_relationships(self, eid: str, limit: int = 12) -> list[dict]:
        """The subjects most associated with one roster entity, enriched with who they are."""
        out: list[dict] = []
        for r in self.relationships.for_entity(eid, limit=limit):
            e = self.roster.get(r["id"])
            if e is None:
                continue
            out.append({**r, "cls": e["cls"], "snapshot": e["snapshot"],
                        "plate": e.get("plate"), "cam": e.get("cam")})
        return out

    def entity_ego_graph(self, eid: str, limit1: int = 8, limit2: int = 5) -> dict:
        """The subject's local relationship network for the profile page: the subject at the
        centre, the people/vehicles they're most associated with (1 hop), and in turn WHO THOSE
        are linked to (2 hops) — so an investigator sees not just direct contacts but the circle
        around them. Nodes carry a photo; edges carry co-occurrence strength."""
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        seen_edges: set = set()

        def add_node(nid: str, hop: int) -> None:
            if nid in nodes:
                nodes[nid]["hop"] = min(nodes[nid]["hop"], hop)
                return
            e = self.roster.get(nid)
            nodes[nid] = {"id": nid, "hop": hop,
                          "cls": e["cls"] if e else "person",
                          "snapshot": e["snapshot"] if e else None,
                          "plate": e.get("plate") if e else None}

        def add_edge(a: str, b: str, r: dict) -> None:
            if a == b:
                return
            k = frozenset((a, b))
            if k in seen_edges:
                return
            seen_edges.add(k)
            edges.append({"a": a, "b": b, "count": r.get("count", 0),
                          "confidence": r.get("confidence", 0), "cameras": r.get("cameras", [])})

        add_node(eid, 0)
        l1 = self.relationships.for_entity(eid, limit=limit1)
        for r in l1:
            add_node(r["id"], 1)
            add_edge(eid, r["id"], r)
        for r in l1:
            for r2 in self.relationships.for_entity(r["id"], limit=limit2):
                if r2["id"] == eid:
                    continue
                add_node(r2["id"], 2)
                add_edge(r["id"], r2["id"], r2)
        return {"center": eid, "nodes": list(nodes.values()), "edges": edges}

    def camera_dna(self) -> list[dict]:
        """Per-camera behavioural profile + reputation, one row per camera seen this session."""
        names = {s.id: s.name for s in self.db.list_sources()}
        return self.cam_profiles.all(names)

    def build_suggestions(self) -> list[dict]:
        """Proactive, data-driven recommendations: alert rules for behaviours a camera keeps
        seeing (that have no rule yet), and camera-improvement advice from the reputation
        signals. Explainable and high-confidence — never invented. Pure logic lives in
        server.suggestions; here we just gather the evidence and delegate."""
        now = time.time()
        days = int(self.config.get("events.retention_days", 7))
        start = now - days * 86400
        names = {s.id: s.name for s in self.db.list_sources()}
        existing = {(r.event_type, r.source_id) for r in self.db.list_alert_rules()}
        min_n = int(self.config.get("suggestions.min_events", 5))
        counts_by_source: dict[int, dict[str, int]] = {}
        for sid in names:
            try:
                counts_by_source[sid] = self.db.event_type_counts(start, now, source_id=sid)
            except Exception:  # noqa: BLE001
                continue
        out = suggestions.alert_suggestions(
            counts_by_source, names, existing, min_events=min_n, retention_days=days)
        # Proactive zone detection: propose where a watch zone belongs from the activity hotspot.
        zone_by_source: dict[int, dict] = {}
        for sid in names:
            try:
                z = self.cam_profiles.suggested_zone(sid)
                if z:
                    zone_by_source[sid] = z
            except Exception:  # noqa: BLE001
                continue
        out.extend(suggestions.zone_suggestions(zone_by_source, names, existing))
        out.extend(suggestions.camera_suggestions(self.cam_profiles.all(names)))
        # FOG OF WAR: a persistent blind spot is a coverage gap with a named remedy, which makes
        # it a work item rather than a picture. Surfacing it here is what turns the observability
        # field into something that gets acted on.
        spots_by_source: dict[int, list[dict]] = {}
        for sid in names:
            try:
                spots = [s for s in self.coverage.blind_spots(sid)
                         if s.get("persistent") and not s.get("dismissed")]
                if spots:
                    spots_by_source[sid] = spots
            except Exception:  # noqa: BLE001
                continue
        out.extend(suggestions.coverage_suggestions(spots_by_source, names))
        return out

    def relationship_graph(self, min_count: int = 2, limit: int = 300) -> dict:
        """The social graph: nodes (with a photo) and weighted association edges."""
        g = self.relationships.graph(min_count=min_count, limit=limit)
        nodes = []
        for nid in g["nodes"]:
            e = self.roster.get(nid)
            if e is not None:
                nodes.append({"id": nid, "cls": e["cls"], "snapshot": e["snapshot"],
                              "plate": e.get("plate")})
        valid = {n["id"] for n in nodes}
        edges = [e for e in g["edges"] if e["a"] in valid and e["b"] in valid]
        return {"nodes": nodes, "edges": edges}

    # -- long-term identity: subjects, dossiers, reconstruction (features 5/6/7) --------------
    def _subj_url(self, p: Any) -> str | None:
        if not p:
            return None
        try:
            return f"/snapshots/{Path(p).relative_to(self._snap_dir).as_posix()}"
        except Exception:  # noqa: BLE001
            s = str(p).replace("\\", "/")
            return "/snapshots/" + s.split("/snapshots/")[-1] if "/snapshots/" in s else None

    def subjects_list(self, cls: str | None = None, limit: int = 200,
                      order: str = "last_seen") -> list[dict]:
        """Persisted long-term subjects (repeat visitors) with a resolved photo URL."""
        if self.subject_store is None:
            return []
        out = []
        for s in self.subject_store.list(cls=cls, limit=limit, order=order):
            s = dict(s)
            s["snapshot"] = self._subj_url(s.pop("snapshot_path", None))
            out.append(s)
        return out

    def subject_dossier(self, subject_id: int) -> dict | None:
        """One subject's full dossier: gallery photo, per-camera + hour-of-day patterns, sightings."""
        if self.subject_store is None:
            return None
        d = self.subject_store.dossier(int(subject_id))
        if d is None:
            return None
        d = dict(d)
        d["snapshot"] = self._subj_url(d.pop("snapshot_path", None))
        d["sightings"] = [{**si, "snapshot": self._subj_url(si.get("snapshot_path"))}
                          for si in d.get("sightings", [])]
        return d

    def reconstruct_subject(self, subject_id: int, max_frames: int = 16) -> dict:
        """Feature 7: fuse a subject's distinct sighting crops into one super-resolved image."""
        if self.subject_store is None:
            return {"image": None, "reason": "disabled"}
        d = self.subject_store.dossier(int(subject_id))
        if d is None:
            return {"image": None, "reason": "unknown"}
        seen, crops = set(), []
        for si in d.get("sightings", []):
            p = si.get("snapshot_path")
            if not p or p in seen:
                continue
            seen.add(p)
            img = cv2.imread(str(p))
            if img is not None:
                crops.append(img)
            if len(crops) >= max_frames:
                break
        return self._encode_reconstruct(crops)

    def reconstruct_plate(self, det_id: str) -> dict:
        """Feature 7: fuse the many tight plate crops captured for one vehicle track + re-read it."""
        crops = self.plates.recent_crops(det_id) if hasattr(self.plates, "recent_crops") else []
        res = self._encode_reconstruct(crops)
        if res.get("image") and hasattr(self, "_yolo"):
            try:
                from .plate_ocr import default_plate_reader
                reader = default_plate_reader()
                raw = reader(cv2.imdecode(np.frombuffer(base64.b64decode(res["image"]), np.uint8),
                                          cv2.IMREAD_COLOR))
                if raw:
                    from match.anpr.normalize import normalize_plate
                    res["plate"] = normalize_plate(raw[0][0])
            except Exception:  # noqa: BLE001
                pass
        return res

    def _encode_reconstruct(self, crops: list) -> dict:
        if len(crops) < 2:
            return {"image": None, "reason": "not_enough_frames", "frames_offered": len(crops)}
        from .reconstruct import reconstruct as _recon
        res = _recon(crops, scale=2.0, sr=self._sr)
        if res is None:
            return {"image": None, "reason": "fusion_failed", "frames_offered": len(crops)}
        ok, buf = cv2.imencode(".jpg", res["image"], [int(cv2.IMWRITE_JPEG_QUALITY), 94])
        return {
            "image": base64.b64encode(buf.tobytes()).decode("ascii") if ok else None,
            "method": res["method"], "frames_used": res["frames_used"],
            "frames_offered": res["frames_offered"],
        }

    def _accumulate_gait(self, r: Any, img: Any, poses: list, now: float) -> None:
        """Feed tracked person skeletons into the gait tracker; when a track has walked enough frames,
        persist its gait + soft-biometrics into the identity store so it fuses with appearance re-ID.
        Fully guarded + throttled so it never disturbs the analysis hot path."""
        if self.gait_tracker is None or self.subject_store is None:
            return
        persons = []
        for group in (getattr(r, "detections", {}) or {}).values():   # detections is {category: [det]}
            for d in group:
                if d.track_id is None or _CATEGORY_CLS.get(d.category, "object") != "person":
                    continue
                persons.append((f"TK{self._source_id}.{d.track_id}", tuple(float(v) for v in d.bbox)))
        if not persons:
            return
        self.gait_tracker.update(persons, poses, now)
        h, w = img.shape[:2]
        for key, pbox in persons:
            if now - self._gait_recorded.get(key, 0.0) < 15.0:
                continue
            desc = self.gait_tracker.descriptor(key)
            if desc is None:
                continue
            self._gait_recorded[key] = now
            try:
                x1, y1 = max(0, int(pbox[0])), max(0, int(pbox[1]))
                x2, y2 = min(w, int(pbox[2])), min(h, int(pbox[3]))
                crop = img[y1:y2, x1:x2]
                emb = self._roster_embed(crop, "person") if crop.size else None
                if emb is None:
                    continue
                snap = self.snapshots.save(crop.copy(), prefix="gait")
                sb = desc.get("soft_bio", {})
                attrs = {"cadence_hz": desc.get("cadence_hz"), "build_ratio": sb.get("shoulder_hip"),
                         "leg_ratio": sb.get("leg_torso"), "gait": True}
                self.subject_store.record(
                    "person", appearance=emb, gait=desc["vector"], now=now, snapshot_path=str(snap),
                    cam=self._source_name(self._source_id), source_id=self._source_id,
                    attrs={k: v for k, v in attrs.items() if v is not None})
            except Exception:  # noqa: BLE001
                pass

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
            if cls == "person":
                ph, pw = crop.shape[:2]   # torso ROI: below the head, centre only (see _appearance)
                band = crop[int(ph * 0.18):int(ph * 0.55), int(pw * 0.25):int(pw * 0.75)]
            else:
                band = crop
            if cls == "person" and getattr(band, "size", 0) and skin_fraction(band) > 0.6:
                attrs = {"upper_color": "bare skin"}   # shirtless: report it, not a made-up shirt colour
            else:
                col, cconf = dominant_color_name_conf(band, ignore_skin=(cls == "person")) if getattr(band, "size", 0) else ("unknown", 0.0)
                attrs = {"upper_color": col} if (col != "unknown" and cconf >= 0.42) else {}
            if cls == "vehicle":
                hit = self.make.classify(crop)   # confidence-gated brand; None if unsure
                if hit:
                    attrs["make"] = hit[0]
                bt = self.bodytype.classify(crop)   # sedan / hatchback / SUV / ... ; None if unsure
                if bt:
                    attrs["bodytype"] = bt[0]
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
        if status == "reconnecting" and self._source_id is not None:
            self.cam_profiles.note_reconnect(self._source_id)   # dents the camera's reputation
        self.set_conn(mapping.get(status, self._conn))

    @staticmethod
    def _appearance(img: Any, x1: int, y1: int, x2: int, y2: int, cls: str, frame_h: int) -> dict | None:
        """Lightweight per-detection appearance attrs (colour + height band) for the
        live stream — feeds the tracking panel and client-side re-identification."""
        x1, y1 = max(0, x1), max(0, y1)
        if x2 <= x1 or y2 <= y1:
            return None
        bh, bw = y2 - y1, x2 - x1
        attrs: dict[str, Any] = {}
        if cls == "person":  # height only makes sense for people (vehicles use body-type instead)
            # Perspective-normalize apparent size by the foot position: a person near the bottom of
            # the frame (close) spans more pixels than the same person near the horizon (far). Dividing
            # by the foot fraction keeps same-stature people comparable across depth, instead of the
            # raw box fraction that read almost everyone as "short". Uncalibrated => approximate cm.
            fy = min(1.0, y2 / max(1, frame_h))
            hn = bh / max(1, frame_h)
            stature = hn / max(0.30, fy)
            # Gain recalibrated (was *130, which saturated almost everyone at the 200 cap): a
            # normally-framed adult (stature ~0.75-0.85) now lands ~168-175 cm instead of 200.
            cm = int(round(max(150.0, min(205.0, 120.0 + stature * 62.0))))
            attrs["height_cm"] = cm
            attrs["height"] = "short" if cm < 168 else ("tall" if cm > 182 else "medium")
        if bh >= 24 and bw >= 12:  # skip tiny/far crops where colour is unreliable
            if cls == "person":
                # Torso ROI only: skip the head (top ~18%) and trim the arms / background people at
                # the sides (keep the centre 50%), so the shirt drives the colour rather than face,
                # hair, skin and whatever is standing behind them.
                roi = img[y1 + int(bh * 0.18):y1 + int(bh * 0.55), x1 + int(bw * 0.25):x1 + int(bw * 0.75)]
            else:
                roi = img[y1:y2, x1:x2]
            try:
                if getattr(roi, "size", 0):
                    if cls == "person" and skin_fraction(roi) > 0.6:
                        attrs["upper_color"] = "bare skin"   # shirtless: report it, don't invent a shirt colour
                    else:
                        name, conf = dominant_color_name_conf(roi, ignore_skin=(cls == "person"))
                        # Only assert a colour when the crop is clearly that colour: on a murky/contaminated
                        # crop the operator would far rather see nothing than a confident wrong guess.
                        if name != "unknown" and conf >= 0.42:
                            attrs["upper_color"] = name
            except Exception:  # noqa: BLE001
                pass
        return attrs

    def _tap_frame(self, frame: Any) -> None:
        """Display tap: runs on the CAPTURE thread for every captured frame. It only STORES the newest
        raw frame (a reference assignment, instant), so it never slows capture. A dedicated encoder
        thread turns it into a JPEG at a steady rate, so the live feed plays at camera rate rather than
        stuttering at the analysis rate. Boxes are streamed separately and interpolated client-side."""
        self._latest_raw = frame.image
        self._raw_seq += 1

    def _display_encoder_loop(self) -> None:
        """Encode the newest raw frame to JPEG at a steady 30 fps, off the capture and analysis threads.
        DEADLINE-paced: the loop absorbs its own ~8 ms encode into the frame interval (a naive
        sleep(1/30)+encode ran at only ~22 fps), so the display source is genuinely 30 fps."""
        # Run this thread ABOVE_NORMAL so its (GIL-releasing) imencode wins the CPU over the analysis
        # / harvester threads under load; that is what keeps the feed smooth instead of frame-by-frame.
        from core.thread_priority import set_current_thread_priority, ABOVE_NORMAL
        set_current_thread_priority(ABOVE_NORMAL)
        target = 1.0 / 30.0
        nxt = time.perf_counter()
        while self._disp_run:
            nxt += target
            if self._raw_seq != self._enc_seq:
                img = self._latest_raw
                if img is not None:
                    self._enc_seq = self._raw_seq
                    try:
                        ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
                        if ok:
                            self._latest_raw_jpeg = buf.tobytes()
                    except Exception:  # noqa: BLE001
                        pass
            rem = nxt - time.perf_counter()
            if rem > 0:
                time.sleep(rem)
            else:
                nxt = time.perf_counter()   # fell behind; resync rather than spiral

    def _start_display_encoder(self) -> None:
        if self._disp_thread is not None:
            return
        # Sharpen thread scheduling so the encoder + event loop share the GIL smoothly: a shorter GIL
        # switch interval, and (Windows) a 1 ms timer so time.sleep is precise instead of ~15 ms.
        try:
            import sys
            sys.setswitchinterval(0.0005)
        except Exception:  # noqa: BLE001
            pass
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)   # no-op off Windows (AttributeError caught)
        except Exception:  # noqa: BLE001
            pass
        self._disp_run = True
        self._disp_thread = threading.Thread(target=self._display_encoder_loop, name="display-encoder", daemon=True)
        self._disp_thread.start()

    def _on_result(self, r: AnalysisResult) -> None:
        img = r.frame.image
        self._latest_img = img
        _enc_now = time.time()
        h, w = img.shape[:2]
        sid = self._source_id
        # Background PLATE (EMA): moving objects average out, leaving the true static scene so the 3D
        # view can show what's really BEHIND them. It is only consumed when the spatial view is built,
        # so update it every 3rd frame (a faster EMA rate compensates) instead of a 26 ms float32 pass
        # on every frame.
        self._bgp_ctr = (self._bgp_ctr + 1) % 3
        if self._bgp_ctr == 0:
            if (self._bg_plate is None or sid != self._bg_plate_sid or self._bg_plate.shape != img.shape):
                self._bg_plate = img.astype(np.float32).copy(); self._bg_plate_sid = sid; self._bg_plate_n = 1
            else:
                self._bg_plate += 0.06 * (img.astype(np.float32) - self._bg_plate); self._bg_plate_n += 1
        # The display feed is served at capture rate by the frame tap; this analysed-frame JPEG is only
        # a warm fallback now, so encode it at ~5 fps rather than on every analysed frame.
        if _enc_now - self._last_anal_enc > 0.2:
            self._last_anal_enc = _enc_now
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
        prof_dets: list = []   # (cls, conf) for the camera DNA / reputation profile
        prof_points: list = []  # normalized foot-points -> density grid for auto zone suggestions
        pose_targets: list = []  # (det_id, px bbox) for people, so the pose pass can attach facing
        for group in r.detections.values():
            for d in group:
                x1, y1, x2, y2 = d.bbox
                cls = _CATEGORY_CLS.get(d.category, "object")
                weapon = d.category == "weapon"
                if cls in ("person", "vehicle", "animal"):
                    prof_dets.append((cls, float(d.confidence)))
                    if cls in ("person", "vehicle") and w > 0 and h > 0:
                        prof_points.append(((x1 + x2) / 2.0 / w, y2 / h))
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
                # Appearance (dominant colour + skin fraction + height) is stable per subject, but the
                # numpy/k-means work ran for EVERY detection EVERY frame, holding the GIL and helping
                # starve the display encoder. Cache per track and refresh only every ~10 frames.
                _ac = self._appearance_cache.get(det["id"])
                if _ac is not None and (self._pose_ctr - _ac[1]) < 10:
                    attrs = _ac[0]
                else:
                    attrs = self._appearance(img, int(x1), int(y1), int(x2), int(y2), cls, h)
                    if len(self._appearance_cache) > 800:
                        self._appearance_cache.clear()   # bound growth over a long session
                    self._appearance_cache[det["id"]] = (attrs, self._pose_ctr)
                if attrs:
                    det["attrs"] = attrs
                # behavioural intent (why) — estimated from motion, for people
                if cls == "person" and d.track_id is not None:
                    it = self.intent.update(d.track_id, (x1, y1, x2, y2), now,
                                            frame_diag=float((w * w + h * h) ** 0.5))
                    if it:
                        det["intent"] = it
                if cls == "person":
                    pose_targets.append((det["id"], (x1, y1, x2, y2)))
                    fc = self._facing.get(det["id"])   # facing is computed off the low-rate pose pass
                    if fc is not None and now - fc[1] < 2.0:   # so it is cached and read on later frames
                        det["facing"] = fc[0]
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
                            self.live_bodytype.offer(det["id"], crop, now)  # body-type, off-thread
                        plate = self.plates.plate_for(det["id"])
                        if plate:
                            det["plate"] = plate[0]
                            self._check_plate_watch(plate[0], self._source_name(self._source_id))
                        make = self.live_make.make_for(det["id"])
                        if make:
                            det["make"] = make
                        bodytype = self.live_bodytype.make_for(det["id"])
                        if bodytype:
                            det["bodytype"] = bodytype
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
        self.live_bodytype.prune(vehicle_ids)
        self.speed.prune(now)
        self.intent.prune(now)
        if self._source_id is not None:   # accumulate this camera's DNA / reputation
            self.cam_profiles.observe_frame(
                self._source_id, brightness=float(getattr(r.metrics, "brightness", 0.0)),
                motion=float(r.motion_percent), fps=float(r.fps), dets=prof_dets,
                points=prof_points)
        # FOG OF WAR — the empirical channel. Where tracks are born and where they die, away
        # from the frame border, is the only honest measure of where this camera fails.
        self._observe_coverage(dets, now)
        # GRAIN — score each subject's MOVEMENT against what this place normally does, and
        # attach it to the detection so the live gauge has something to show. Two dict writes
        # and four log-density evaluations per track: it rides on the existing pass.
        self._observe_grain(dets, now)
        # Throttle the detections emit (~15 Hz) so it can never saturate the single event loop and
        # starve /stream. Boxes are interpolated client-side, so a lower cadence is invisible.
        if _enc_now - self._last_det_push > 0.066:
            self._last_det_push = _enc_now
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

        # keypoint pose: ONE inference feeds both hand-raise behaviours and gait/soft-biometrics
        self._pose_ctr += 1
        run_gait = self.gait_tracker is not None and self._pose_ctr % 5 == 0
        if self._pose_ctr % 10 == 0 or run_gait:
            poses = self.pose_kp.detect_pose(img)
            # facing / attention heading per person -> cached by det id, read on later frames (the
            # detections for THIS frame are already emitted). Match each skeleton to the person box
            # it overlaps most. Feeds the Social X-ray overlay.
            if poses and pose_targets:
                for pose in poses:
                    deg = self.pose_kp.facing(pose)
                    if deg is None:
                        continue
                    px1, py1, px2, py2 = pose["bbox"]
                    best_id, best_iou = None, 0.0
                    for did, (bx1, by1, bx2, by2) in pose_targets:
                        ix1, iy1 = max(px1, bx1), max(py1, by1)
                        ix2, iy2 = min(px2, bx2), min(py2, by2)
                        iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
                        inter = iw * ih
                        if inter <= 0:
                            continue
                        union = (px2 - px1) * (py2 - py1) + (bx2 - bx1) * (by2 - by1) - inter
                        iou = inter / union if union > 0 else 0.0
                        if iou > best_iou:
                            best_iou, best_id = iou, did
                    if best_id is not None and best_iou >= 0.3:
                        self._facing[best_id] = (deg, now)
                for did in [k for k, (_, ts) in self._facing.items() if now - ts > 5.0]:
                    self._facing.pop(did, None)
            if self._pose_ctr % 10 == 0:
                for pose in poses:
                    beh = self.pose_kp.hand_raise(pose, w, h)
                    if beh is None:
                        continue
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
            if run_gait and poses:
                self._accumulate_gait(r, img, poses, now)

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

    # ---- FOG OF WAR ---------------------------------------------------
    def _observe_coverage(self, dets: list[dict], now: float) -> None:
        """Accumulate the empirical channel and raise LOST IN FOG.

        Deliberately cheap: two dict updates per track. The geometry that produces the shadow
        set is only computed when the operator opens the overlay, so a camera nobody is looking
        at costs nothing beyond the counters."""
        if self._source_id is None or not self.config.get("coverage.enabled", True):
            return
        try:
            self.coverage.observe(self._source_id, dets, now)
            if now - self._last_loss_check > 1.0:
                self._last_loss_check = now
                for loss in self.coverage.check_losses(self._source_id, dets, now):
                    cam = self._source_name(self._source_id)
                    self._emit({"t": "alert", "d": {
                        "ts": now * 1000, "severity": "warning", "type": "LOST IN FOG",
                        "summary": (f"{loss['det_id']} entered an unobservable area and has not "
                                    f"reappeared within the expected crossing time"),
                        "cam": cam, "ack": False,
                        "snapshot": self._alert_snapshot(), "clip": self._save_clip(),
                    }})
        except Exception:   # never let an observability channel break the analysis pass
            log.debug("coverage observe failed", exc_info=True)

    # ---- GRAIN --------------------------------------------------------------------------
    def _grain_occluded(self, source_id: int, nx: float, ny: float) -> bool:
        """Is this ground point inside a known FOG OF WAR shadow? Consulted before a track's
        ending is treated as a disappearance."""
        try:
            for sh in (self.coverage._shadows.get(int(source_id)) or []):
                p = sh.get("polygon") or []
                if len(p) >= 4 and p[0][0] <= nx <= p[1][0] and p[0][1] <= ny <= p[2][1]:
                    return True
        except Exception:
            pass
        return False

    def _observe_grain(self, dets: list[dict], now: float) -> None:
        if self._source_id is None or not self.config.get("grain.enabled", True):
            return
        try:
            people = [d for d in dets if d.get("cls") in ("person", "vehicle")
                      and not d.get("coasting")]
            density = len(people)
            for d in people:
                b = d.get("bbox")
                if not b:
                    continue
                nx = float(b[0]) + float(b[2]) / 2.0
                ny = min(0.999, float(b[1]) + float(b[3]))
                aspect = float(b[2]) / max(1e-6, float(b[3]))
                # NOTE: only geometry is passed. Colour, height, plate, make and every other
                # appearance attribute on `d` is deliberately left behind — see server/grain.py.
                self.grain.observe(self._source_id, str(d["id"]), str(d["cls"]), nx, ny, now,
                                   aspect=aspect, density=density)
                c = self.grain.peek(str(d["id"]), now)
                if c is not None:
                    d["conformity"] = c
            for row in self.grain.sweep(now):
                self._emit({"t": "grain", "d": row})
                if row.get("state") == "unusual":
                    self._grain_alert(row)
        except Exception:
            log.debug("grain observe failed", exc_info=True)

    def _grain_alert(self, row: dict) -> None:
        """Raise an alert only for the genuinely rare, and say WHY in the summary rather than
        asserting that something is wrong."""
        thr = float(self.config.get("grain.alert_percentile", 0.1))
        if float(row.get("percentile", 100.0)) > thr:
            return
        cam = self._source_name(self._source_id)
        why = row.get("why") or "moved in a way this place rarely sees"
        self._emit({"t": "alert", "d": {
            "ts": time.time() * 1000, "severity": "warning", "type": "UNUSUAL BEHAVIOUR",
            "summary": f"{row.get('det_id')} {why[0].lower()}{why[1:]} "
                       f"({row.get('percentile', 0):.1f}th percentile for this camera)",
            "cam": cam, "ack": False,
            "snapshot": self._alert_snapshot(), "clip": self._save_clip(),
        }})

    def grain_field(self, sid: str, bucket: int | None = None, cls: str = "person") -> dict:
        if not self.config.get("grain.enabled", True):
            return {"status": None, "reason": "disabled"}
        src = next((s for s in self.db.list_sources() if str(s.id) == str(sid)), None)
        if src is None:
            return {"status": None, "reason": "no_source"}
        try:
            return {"status": self.grain.field(int(src.id), src.name, bucket, cls)}
        except Exception:
            log.exception("grain field failed")
            return {"status": None, "reason": "failed"}

    def grain_ledger(self, sid: str, limit: int = 100, unusual_only: bool = False) -> dict:
        src = next((s for s in self.db.list_sources() if str(s.id) == str(sid)), None)
        if src is None:
            return {"tracks": []}
        return {"tracks": self.grain.ledger(int(src.id), limit, unusual_only)}

    def grain_precedents(self, track_id: int, n: int = 6) -> dict:
        return {"precedents": self.grain.precedents(int(track_id), int(n))}

    def grain_verdict(self, track_id: int, verdict: str | None) -> dict:
        return self.grain.verdict(int(track_id), verdict)

    def grain_mute(self, sid: str, cells: list[int]) -> dict:
        src = next((s for s in self.db.list_sources() if str(s.id) == str(sid)), None)
        if src is None:
            return {"muted": []}
        return {"muted": self.grain.mute(int(src.id), cells)}

    def _coverage_depth(self, sid: str, frame: Any) -> Any:
        """The depth grid for the coverage build, reusing the spatial view's if it is fresh.

        Depth inference contends with the detector and ReID for the GPU, so paying for it twice
        to draw the same shadows would be indefensible."""
        cache = self._depth_cache
        if cache is not None and cache[0] == str(sid) and time.time() - cache[2] < 20.0:
            return cache[1]
        if self._depth is None:
            return None
        h0, w0 = frame.shape[:2]
        work_w = int(self.config.get("spatial.input_width", 768))
        work = (cv2.resize(frame, (work_w, int(work_w * h0 / w0)), interpolation=cv2.INTER_AREA)
                if w0 > work_w else frame)
        disp = self._depth.estimate(work)
        if disp is None:
            return None
        disp01, _dmin, _dmax = spatial.normalize_disparity(
            cv2.resize(disp, (160, max(1, int(160 * work.shape[0] / work.shape[1]))),
                       interpolation=cv2.INTER_AREA))
        self._depth_cache = (str(sid), disp01, time.time())
        return disp01

    def coverage_scene(self, sid: str, task: str | None = None,
                       height: float | None = None) -> dict:
        """The observability field for one camera. Always returns a dict with a specific reason
        on failure, never a 5xx, so the UI can explain itself."""
        if not self.config.get("coverage.enabled", True):
            return {"coverage": None, "reason": "disabled"}
        src = next((s for s in self.db.list_sources() if str(s.id) == str(sid)), None)
        if src is None:
            return {"coverage": None, "reason": "no_source"}
        frame = self._source_frame(src)
        if frame is None:
            return {"coverage": None, "reason": "no_frame"}
        disp01 = self._coverage_depth(str(sid), frame)
        if disp01 is None:
            # Honest degradation: without depth there is no standing-object mask, so the
            # geometric channel is empty. The other three still work and the UI says so.
            log.debug("coverage: no depth field for %s, geometric channel disabled", sid)
        try:
            cov = self.coverage.build(int(src.id), src.name, frame, disp01,
                                      float(self.config.get("spatial.fov_deg", 60.0)),
                                      task=task, target_h=height)
        except Exception:
            log.exception("coverage build failed")
            return {"coverage": None, "reason": "build_failed"}
        cov["depth_backed"] = disp01 is not None
        return {"coverage": cov}

    def blind_spots(self, sid: str) -> dict:
        src = next((s for s in self.db.list_sources() if str(s.id) == str(sid)), None)
        if src is None:
            return {"spots": []}
        try:
            return {"spots": self.coverage.blind_spots(int(src.id))}
        except Exception:
            log.exception("blind spot listing failed")
            return {"spots": []}

    def dismiss_blind_spot(self, spot_id: int, on: bool = True) -> dict:
        self.db.execute("UPDATE blind_spots SET dismissed = ? WHERE id = ?",
                        (1 if on else 0, int(spot_id)))
        return {"ok": True}

    def coverage_report(self, sid: str) -> dict:
        """A signed, printable statement of what this camera can and cannot see.

        This is the artifact that answers 'prove your system works' in a procurement, which is
        a question no VMS vendor can currently answer with a number."""
        res = self.coverage_scene(sid)
        cov = res.get("coverage")
        if cov is None:
            return {"report": None, "reason": res.get("reason", "unavailable")}
        spots = self.blind_spots(sid).get("spots", [])
        persistent = [s for s in spots if s.get("persistent") and not s.get("dismissed")]
        return {"report": {
            "camera": cov["cam"], "sid": str(sid),
            "generated_at": time.time(),
            "task": cov["task"], "target_height_m": cov["target_height_m"],
            "coverage_percent": cov["percent"],
            "fov_deg": cov["fov_deg"], "camera_height_m": cov["camera_height_m"],
            "pitch_deg": cov["pitch_deg"],
            "dori_bands": cov["bands"],
            "blind_spots": [{"name": s["name"], "kind": s["kind"], "area_m2": s["area_m2"],
                             "events": s["events"]} for s in persistent],
            "methodology": (
                "Observability is computed over the OBSERVED ground area only, from four "
                "channels: ray occlusion against the depth-derived standing-object mask, "
                "pixels-per-metre against EN 62676-4 (DORI), local photometric quality, and "
                "measured track mortality. Ranges derive from a pinhole ground-plane model "
                "using an estimated camera height and tilt, so all metre values are "
                "approximate; relative ordering is reliable."),
            "scale_estimated": True,
        }}

    def spatial_scene(self, sid: str, grid_w: int = 320) -> dict:
        """Feature 4 — lift a camera's flat 2D frame into a navigable 3D point cloud.

        Runs Depth Anything V2 on the latest frame, downsamples RGB + depth to a working grid
        (bounded payload), and locates every detected entity in that same frame so the markers
        line up with the cloud. The frontend back-projects the grid through a pinhole model and
        renders it in three.js. Always returns a dict: {"scene": {...}} on success, or
        {"scene": None, "reason": "..."} with a specific cause so the UI can explain itself
        (never a 5xx). Reasons: disabled / no_source / no_frame / depth_unavailable."""
        if not self.config.get("spatial.enabled", True):
            return {"scene": None, "reason": "disabled"}
        src = next((s for s in self.db.list_sources() if str(s.id) == str(sid)), None)
        if src is None:
            return {"scene": None, "reason": "no_source"}
        frame = self._source_frame(src)
        if frame is None:
            return {"scene": None, "reason": "no_frame"}
        # Run depth on a moderate-resolution copy (quality vs. latency), then downscale to grid.
        h0, w0 = frame.shape[:2]
        work_w = int(self.config.get("spatial.input_width", 640))
        if w0 > work_w:
            work = cv2.resize(frame, (work_w, int(work_w * h0 / w0)), interpolation=cv2.INTER_AREA)
        else:
            work = frame
        disp = self._depth.estimate(work)
        if disp is None:
            return {"scene": None, "reason": "depth_unavailable"}
        # Multi-frame temporal fusion (spatial.fuse_frames): grab a few more frames at short
        # intervals and take the per-pixel MEDIAN of their depth. For a fixed camera this averages
        # out the per-frame monocular-depth noise on the static scene (noise ~ 1/sqrt(N)), while the
        # median stays robust to moving objects (a person/car crossing doesn't smear the geometry).
        n_fuse = max(1, int(self.config.get("spatial.fuse_frames", 1)))
        if n_fuse > 1:
            stack = [disp]
            wh = (work.shape[1], work.shape[0])
            for _ in range(n_fuse - 1):
                time.sleep(float(self.config.get("spatial.fuse_delay", 0.1)))   # let a new frame arrive
                fk = self._source_frame(src)
                if fk is None:
                    continue
                wk = fk if fk.shape[:2] == work.shape[:2] else cv2.resize(fk, wh, interpolation=cv2.INTER_AREA)
                dk = self._depth.estimate(wk)
                if dk is not None and dk.shape == disp.shape:
                    stack.append(dk)
            if len(stack) > 1:
                disp = np.median(np.stack(stack, axis=0), axis=0).astype(np.float32)
        grid_w = max(120, min(int(grid_w), 480))
        gh = max(1, int(grid_w * work.shape[0] / work.shape[1]))
        rgb_grid = cv2.resize(work, (grid_w, gh), interpolation=cv2.INTER_AREA)
        disp_grid = cv2.resize(disp, (grid_w, gh), interpolation=cv2.INTER_AREA)
        disp01, dmin, dmax = spatial.normalize_disparity(disp_grid)
        # Hand the same grid to FOG OF WAR rather than making it infer depth again — the GPU is
        # already shared between the detector, ReID and this model.
        self._depth_cache = (str(sid), disp01.copy(), time.time())
        entities, boxes = self._spatial_entities(work, disp, dmin, dmax)
        # Geometric scene completion: reconstruct the occluded background behind foreground
        # objects as a real surface (inpainted depth + texture), shipped as a second mesh layer.
        bg_rgb = bg_disp = None
        if self.config.get("spatial.complete", True):
            # inpaint behind detector boxes AND depth-derived standing objects, so a background
            # layer is produced even with no detections (fills seams behind buildings/clutter).
            fgmask = spatial.foreground_mask(disp01)
            if boxes or bool(fgmask.any()):
                # a warmed-up background plate (same camera) supplies the REAL scene behind moving
                # objects; the depth is filled by extending the ground plane (both in complete_background).
                plate_grid = None
                if (self._bg_plate is not None and str(self._bg_plate_sid) == str(sid)
                        and self._bg_plate_n >= 60 and self._bg_plate.shape[:2] == frame.shape[:2]):
                    plate_grid = cv2.resize(self._bg_plate.astype(np.uint8), (grid_w, gh), interpolation=cv2.INTER_AREA)
                bg_rgb, bg_disp = spatial.complete_background(
                    rgb_grid, disp01, boxes, extra_mask=fgmask, bg_texture=plate_grid)
        scene = spatial.encode_scene(
            rgb_grid, disp01, entities, fov=float(self.config.get("spatial.fov_deg", 60.0)),
            cam=src.name, sid=str(sid), ts=time.time() * 1000.0, bg_rgb=bg_rgb, bg_disp01=bg_disp)
        # High-res texture: a crisper copy of the SAME framed frame (same crop/aspect as the grid),
        # so the browser can UV-map it onto the coarse depth mesh — full-detail texture, light mesh.
        import base64 as _b64
        tw = min(w0, int(self.config.get("spatial.texture_width", 1280)))
        texframe = frame if w0 <= tw else cv2.resize(frame, (tw, int(tw * h0 / w0)), interpolation=cv2.INTER_AREA)
        ok_t, texjpg = cv2.imencode(".jpg", texframe, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if ok_t:
            scene["tex_image"] = _b64.b64encode(texjpg.tobytes()).decode("ascii")
        return {"scene": scene}

    def _reel_scene_from_frame(self, frame: Any, cam_name: str, sid: str, grid_w: int) -> dict | None:
        """A LIGHT spatial scene from one specific frame, for HoloReel: depth + grid + texture only
        (no temporal fusion, no background completion, no entity markers), so building 24 of them in a
        row is fast enough to feel like a captured clip."""
        h0, w0 = frame.shape[:2]
        work_w = int(self.config.get("spatial.input_width", 640))
        work = cv2.resize(frame, (work_w, int(work_w * h0 / w0)), interpolation=cv2.INTER_AREA) if w0 > work_w else frame
        disp = self._depth.estimate(work)
        if disp is None:
            return None
        grid_w = max(120, min(int(grid_w), 480))
        gh = max(1, int(grid_w * work.shape[0] / work.shape[1]))
        rgb_grid = cv2.resize(work, (grid_w, gh), interpolation=cv2.INTER_AREA)
        disp_grid = cv2.resize(disp, (grid_w, gh), interpolation=cv2.INTER_AREA)
        disp01, _dmin, _dmax = spatial.normalize_disparity(disp_grid)
        scene = spatial.encode_scene(
            rgb_grid, disp01, [], fov=float(self.config.get("spatial.fov_deg", 60.0)),
            cam=cam_name, sid=str(sid), ts=time.time() * 1000.0, bg_rgb=None, bg_disp01=None)
        import base64 as _b64
        tw = min(w0, int(self.config.get("spatial.texture_width", 1280)))
        texframe = frame if w0 <= tw else cv2.resize(frame, (tw, int(tw * h0 / w0)), interpolation=cv2.INTER_AREA)
        ok_t, texjpg = cv2.imencode(".jpg", texframe, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if ok_t:
            scene["tex_image"] = _b64.b64encode(texjpg.tobytes()).decode("ascii")
        return scene

    def _reel_raw_frames(self, src: Any, n: int, span: float) -> list:
        """The N raw frames a HoloReel is built from, SPREAD over ~`span` seconds so the replay
        shows real motion. Prefers the live capture ring (what the operator is watching); if that
        is not advancing (no live capture for this source) it reads a consecutive burst straight
        from the source and sub-samples it, so the reel still MOVES instead of freezing on one frame."""
        stride = span / max(1, n - 1)
        # Is the live capture ring actually advancing for this source right now?
        live = str(src.id) == str(self._source_id) and self._latest_raw is not None
        if live:
            seq0 = self._raw_seq
            t_end = time.time() + 0.35
            while self._raw_seq == seq0 and time.time() < t_end:
                time.sleep(0.01)
            live = self._raw_seq != seq0
        if live:
            raws: list = []
            last_seq = -1
            next_due = time.time()
            for _ in range(n):
                now = time.time()
                if now < next_due:                    # pace so successive grabs stay >= stride apart
                    time.sleep(next_due - now)
                waited = 0                            # then wait for a genuinely NEW capture (distinct frame)
                while self._raw_seq == last_seq and waited < 60:
                    time.sleep(0.008); waited += 1
                last_seq = self._raw_seq
                next_due = time.time() + stride
                f = self._latest_raw
                if f is None:
                    break
                raws.append(f.copy())
            if len(raws) >= 2:
                return raws
        # Fallback: live ring is stalled. Read 3x consecutive frames off the source and sub-sample
        # to N spanning the window, so the reel is a real moving clip (a different moment, but moving).
        over = self._grab_burst(src, n * 3)
        if len(over) >= 2:
            idx = sorted({round(i * (len(over) - 1) / (n - 1)) for i in range(n)})
            return [over[j].copy() for j in idx]
        f = self._source_frame(src)                    # last resort: whatever single frame exists
        return [f.copy()] if f is not None else []

    def spatial_reel(self, sid: str, n: int = 28, grid_w: int = 256) -> dict:
        """HoloReel capture: grab N DISTINCT raw frames SPREAD over a few seconds of real time, and
        reconstruct 3D for each. Returns {"frames": [scene, ...]}.

        The frames are spaced across `spatial.reel_span_s` seconds (via `_reel_raw_frames`) so the
        replay shows real motion. (Grabbing all N back to back crammed them into under a second, so
        the reel looked frozen even though every frame differed.)"""
        if not self.config.get("spatial.enabled", True):
            return {"frames": [], "reason": "disabled"}
        src = next((s for s in self.db.list_sources() if str(s.id) == str(sid)), None)
        if src is None or self._depth is None:
            return {"frames": [], "reason": "no_source"}
        n = max(2, min(int(n), 48))
        span = float(self.config.get("spatial.reel_span_s", 3.0))   # real-time window the reel spans
        raws = self._reel_raw_frames(src, n, span)
        frames: list = []
        for f in raws:
            try:
                sc = self._reel_scene_from_frame(f, src.name, sid, grid_w)
            except Exception:  # noqa: BLE001
                sc = None
            if sc is not None:
                frames.append(sc)
        return {"frames": frames}

    def _spatial_entities(self, frame: Any, disp: Any, dmin: float,
                          dmax: float) -> tuple[list[dict], list[tuple[float, float, float, float]]]:
        """Detected people/vehicles/objects in `frame`: (entity markers, normalized boxes). Each
        marker has a normalized centre + a depth sample so it drops into the 3D scene; the boxes
        drive background completion (what to inpaint behind)."""
        det = self._yolo or self._roster_det
        if det is None:
            return [], []
        h, w = frame.shape[:2]
        try:
            dets = det.detect_crop(frame, conf=float(self.config.get("spatial.detect_conf", 0.3)))
        except Exception:  # noqa: BLE001
            return [], []
        out: list[dict] = []
        boxes: list[tuple[float, float, float, float]] = []
        for idx, d in enumerate(dets):
            x1, y1, x2, y2 = d.bbox
            cls = _CATEGORY_CLS.get(d.category, "object")
            out.append({
                "id": f"{cls[:2].upper()}{idx:02d}", "cls": cls,
                "cx": (x1 + x2) / 2.0 / w, "cy": (y1 + y2) / 2.0 / h,
                "depth": spatial.entity_depth(disp, (x1, y1, x2, y2), (w, h), dmin, dmax),
                "conf": round(float(d.confidence), 2), "label": (d.label or cls).upper(),
            })
            boxes.append((x1 / w, y1 / h, x2 / w, y2 / h))
        return out, boxes

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
        """Latest BGR frame for a source. For the active camera, prefer the newest RAW capture
        frame (`_latest_raw`, ~30 fps, always present while the feed plays) over the analysed frame
        (`_latest_img`, only ~3 fps and often None between analysis passes) — the analysed frame
        being None is why VQA / 'look closer' used to answer 'could not read frame'. Otherwise fall
        back to the warm thumbnail relay (decoded)."""
        if str(s.id) == str(self._source_id):
            if self._latest_raw is not None:
                return self._latest_raw
            if self._latest_img is not None:
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
            clip_url = self._save_clip()
            # persist BOTH the snapshot and the video clip on the alert row, so history/replay works
            # for past alerts (previously the clip lived only in the transient WS message).
            alert.clip_path = clip_url
            if not alert.snapshot_path:
                alert.snapshot_path = snap_url
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
                "snapshot": snap_url, "clip": clip_url, "mark": mark,
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
        """POV frame for a specific camera. The active camera returns its full-rate DISPLAY frame
        (the capture-thread tap) so the video is smooth, falling back to the analysed JPEG until the
        first raw frame lands; any other camera returns the persistent warm relay frame — one
        connection per camera, referenced everywhere, so switching feeds never drops to a blank."""
        try:
            same = str(self._source_id) == str(source_id)
        except Exception:  # noqa: BLE001
            same = False
        if same:
            j = self._latest_raw_jpeg or self._latest_jpeg
            if j is not None:
                return j
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
        self._disp_run = False
        self.live_make.stop()
        self.live_bodytype.stop()
        self.thumbs.stop_all()
        try:
            self._unsub()
            self.event_recorder.close()
        except Exception:  # noqa: BLE001
            pass
        self.db.close()
