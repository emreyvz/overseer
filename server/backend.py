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
from camera.frame_buffer import FrameBuffer
from camera.health import HealthMonitor
from camera.stream_reader import StreamReader
from core.config import load_config
from core.pipeline import AnalysisResult, AnalysisWorker, EventRecorder
from events.bus import EventBus
from events.types import Event
from forensic.palette import dominant_color_name
from objects.monitor import ObjectMonitor
from plugins.manager import PluginManager
from pose.monitor import PoseMonitor
from storage.database import Database
from storage.recorder import Recorder
from storage.snapshots import SnapshotService
from trajectory.monitor import TrajectoryMonitor
from vision.motion import MotionDetector
from zones.monitor import ZoneMonitor
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
        self._ensure_coords()

        # Behavioural monitors (constructed once, reset per source).
        self.zones = ZoneMonitor(self.config)
        self.trajectory = TrajectoryMonitor(self.config)
        self.pose = PoseMonitor(self.config)
        self.objects = ObjectMonitor(self.config)
        self.thumbs = ThumbHub(cache_dir=self.data_dir / "thumbs")  # per-camera preview relay + persistent cache
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

    def _source_name(self, sid: int | None) -> str:
        if sid is None:
            return "—"
        for s in self.db.list_sources():
            if s.id == sid:
                return s.name
        return str(sid)

    # ---- sources / status --------------------------------------------
    def sources_payload(self) -> list[dict[str, Any]]:
        out = []
        for s in self.db.list_sources():
            out.append({
                "id": str(s.id), "name": s.name, "url": s.url,
                "health": "online" if s.id == self._source_id else "offline",
                "coords": [s.map_x, s.map_y] if s.map_x is not None and s.map_y is not None else None,
                "fps": 0.0,
            })
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

            from .ytstream import is_stream_url, resolve_stream
            if is_stream_url(source.url):
                # YouTube live → resolve to a direct HLS URL, read via FFMPEG (VideoCapture).
                from .rtsp import RtspReader
                media = resolve_stream(source.url)
                if media is None:
                    log.warning("connect: could not resolve YouTube source %s", source.url)
                    self.set_conn("offline")
                    self._emit({"t": "alert", "d": {
                        "ts": time.time() * 1000, "severity": "warning", "type": "YOUTUBE BLOCKED",
                        "summary": "YouTube bot-check — add config/youtube_cookies.txt (see README)",
                        "cam": source.name, "ack": False, "snapshot": None, "clip": None,
                    }})
                    return
                self._reader = RtspReader(media, self._buffer, on_status=self._on_status)
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
            )
            for det in create_yolo_detectors(self.config, backend):
                plugins.register(det)
            self._yolo = backend  # handle for one-shot 'look closer' inference
            log.info("YOLO detectors online")
        except Exception as exc:  # noqa: BLE001 - degrade gracefully
            log.warning("YOLO unavailable, motion-only: %s", exc)

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
                dets.append(det)
                idx += 1
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

    def _save_clip(self) -> str | None:
        """Write the rolling frame window to a short MP4 — the moment an incident
        happened — so it can be replayed from the alert even after it's over."""
        frames = list(self._clip_ring)
        if len(frames) < 5:
            return None
        try:
            h, w = frames[0].shape[:2]
            clips_dir = self._snap_dir / "clips"
            clips_dir.mkdir(parents=True, exist_ok=True)
            name = f"clip_{int(time.time() * 1000)}.mp4"
            # avc1 (H.264) so the clip plays in the in-app <video>; mp4v does not.
            vw = cv2.VideoWriter(str(clips_dir / name), cv2.VideoWriter_fourcc(*"avc1"), 10.0, (w, h))
            if not vw.isOpened():  # fall back if this build lacks the H.264 encoder
                vw = cv2.VideoWriter(str(clips_dir / name), cv2.VideoWriter_fourcc(*"mp4v"), 10.0, (w, h))
            for f in frames:
                vw.write(f if f.shape[:2] == (h, w) else cv2.resize(f, (w, h)))
            vw.release()
            return f"/snapshots/clips/{name}"
        except Exception:  # noqa: BLE001
            return None

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

    @staticmethod
    def _gray_world(bgr: Any) -> Any:
        """Gray-world white balance (catalog 14, colour constancy). Each camera has its
        own colour cast; normalising it means the SAME jacket reads the same hue on
        every feed, so cross-camera appearance matching stops drifting on white balance."""
        try:
            b, g, r = cv2.split(bgr.astype(np.float32))
            mb, mg, mr = float(b.mean()) + 1e-6, float(g.mean()) + 1e-6, float(r.mean()) + 1e-6
            mgray = (mb + mg + mr) / 3.0
            b *= mgray / mb; g *= mgray / mg; r *= mgray / mr
            return cv2.merge([b, g, r]).clip(0, 255).astype(np.uint8)
        except Exception:  # noqa: BLE001
            return bgr

    @staticmethod
    def _hs_hist(bgr: Any) -> Any:
        """HS colour histogram of a crop, normalised for comparison."""
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, (0, 30, 25), (180, 255, 255))
        h = cv2.calcHist([hsv], [0, 1], mask, [30, 12], [0, 180, 0, 256])
        cv2.normalize(h, h, 0, 1, cv2.NORM_MINMAX)
        return h

    def _appearance_sig(self, bgr: Any, split: bool = False) -> Any:
        """Appearance signature of a crop, white-balanced first. For people (split=True)
        the crop is cut into an upper (torso) and lower (legs) band, each with its own
        histogram (catalog 15) — far more discriminative than one colour blob, e.g. a
        red-top/black-jeans person no longer matches an all-red one."""
        bgr = self._gray_world(bgr)
        if split and bgr.shape[0] >= 24:
            cut = int(bgr.shape[0] * 0.55)
            return ("split", self._hs_hist(bgr[:cut]), self._hs_hist(bgr[cut:]))
        return ("whole", self._hs_hist(bgr))

    @staticmethod
    def _compare_sig(a: Any, b: Any) -> float:
        """Correlation similarity between two signatures. Split signatures compare
        band-for-band (torso weighted 0.6, legs 0.4); falls back to whole-vs-whole."""
        try:
            if a[0] == "split" and b[0] == "split":
                up = float(cv2.compareHist(a[1], b[1], cv2.HISTCMP_CORREL))
                lo = float(cv2.compareHist(a[2], b[2], cv2.HISTCMP_CORREL))
                return 0.6 * up + 0.4 * lo
            return float(cv2.compareHist(a[-1], b[-1], cv2.HISTCMP_CORREL))
        except Exception:  # noqa: BLE001
            return -1.0

    def visual_match(self, entity_bgr: Any, kind: str | None = None, thresh: float = 0.42) -> list[dict]:
        """Find a watchlist entity across cameras by APPEARANCE. Crucially, it first
        DETECTS the objects in each frame and only compares against detections of the
        SAME class (car→car, person→person) — so it locks onto the real object, never
        the sky or a wall that merely shares a colour. People are matched by a part-based
        (torso/legs) signature after white balancing, and each camera's winner must beat
        its runner-up by a margin — otherwise it is flagged ambiguous, not asserted."""
        want = {"person": "person", "vehicle": "vehicle", "animal": "animal", "pet": "animal", "object": "object"}.get(kind or "", None)
        split = want == "person"
        try:
            e_sig = self._appearance_sig(entity_bgr, split=split)
        except Exception:  # noqa: BLE001
            return []
        out: list[dict] = []
        for s in self.db.list_sources():
            frame = self._source_frame(s)
            if frame is None or frame.size == 0:
                continue
            fh, fw = frame.shape[:2]
            dets = self._yolo.detect_crop(frame, conf=0.25) if self._yolo is not None else []
            scored: list[tuple] = []
            for d in dets:
                cls = _CATEGORY_CLS.get(d.category, "object")
                if want and cls != want:
                    continue  # only same-class candidates — this kills the sky/wall matches
                x1, y1, x2, y2 = (int(v) for v in d.bbox)
                crop = frame[max(0, y1):y2, max(0, x1):x2]
                if crop.size == 0:
                    continue
                sim = self._compare_sig(e_sig, self._appearance_sig(crop, split=split))
                if sim > -1.0:
                    scored.append((sim, cls, x1, y1, x2, y2))
            if not scored:
                continue
            scored.sort(key=lambda t: -t[0])
            best = scored[0]
            if best[0] < thresh:
                continue
            # Confidence margin: a clear winner beats the next candidate; a near-tie
            # (two similar-looking objects in frame) is honestly reported as ambiguous.
            runner = scored[1][0] if len(scored) > 1 else -1.0
            margin = round(best[0] - runner, 3) if runner > -1.0 else 1.0
            sim, cls, x1, y1, x2, y2 = best
            out.append({"camId": str(s.id), "cam": s.name, "score": round(sim, 3), "cls": cls,
                        "margin": margin, "ambiguous": bool(runner > -1.0 and margin < 0.06),
                        "bbox": [x1 / fw, y1 / fh, (x2 - x1) / fw, (y2 - y1) / fh]})
        out.sort(key=lambda r: -r["score"])
        return out

    def _on_event(self, ev: Event) -> None:
        type_en = ev.type.name.replace("_", " ")
        self._emit({"t": "event", "d": {
            "ts": ev.timestamp * 1000, "type": type_en, "label": (ev.label or "").upper(),
            "conf": ev.confidence, "cam": str(ev.source_id or ""),
        }})
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
            self._emit({"t": "alert", "d": {
                "ts": alert.timestamp * 1000, "severity": alert.severity,
                "type": type_en, "summary": f"{type_en} · {self._source_name(alert.source_id)}",
                "cam": self._source_name(alert.source_id), "ack": False,
                "snapshot": snap_url, "clip": self._save_clip(), "mark": mark,
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
            j = self.thumbs.get_jpeg(src.id, src.url)  # spins up / keeps the relay warm
            if j:
                return j
        return self._latest_jpeg

    def _prewarm_thumbs(self) -> None:
        """Spin up the preview relay for every source at startup so the map has
        thumbnails ready (and refreshes the persistent cache) without waiting."""
        try:
            for s in self.db.list_sources():
                self.thumbs.get_jpeg(s.id, s.url)
        except Exception:  # noqa: BLE001
            pass

    def thumb_jpeg(self, source_id: int) -> bytes | None:
        """Latest raw JPEG for a camera's lightweight preview (item 4)."""
        src = next((s for s in self.db.list_sources() if s.id == source_id), None)
        if src is None:
            return None
        return self.thumbs.get_jpeg(source_id, src.url)

    def reap_thumbs(self) -> None:
        self.thumbs.reap()

    def shutdown(self) -> None:
        self.disconnect()
        self.thumbs.stop_all()
        try:
            self._unsub()
            self.event_recorder.close()
        except Exception:  # noqa: BLE001
            pass
        self.db.close()
