"""Session roster: an anonymous, ReID-deduplicated registry of every person and vehicle
seen across ALL cameras this session, with a representative photo (and, for vehicles, a
plate). A background harvester (RosterHarvester) periodically scans each camera's current
frame — independent of which camera is being actively analysed — detects people/vehicles,
embeds them, and folds them in here. Entries are merged by appearance embedding (cosine),
so the same subject seen again (another frame, another camera) is one anonymous identity,
not a pile of duplicates. Background cutouts use YOLO-seg.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32).reshape(-1)
    b = np.asarray(b, dtype=np.float32).reshape(-1)
    if a.size == 0 or a.shape != b.shape:
        return -1.0
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return -1.0
    return float(np.dot(a, b) / (na * nb))


class SessionRoster:
    def __init__(self, snapshots: Any, snap_dir: Path, seg_backend: Any = None,
                 *, min_area: int = 1400, refresh_ratio: float = 1.4,
                 shot_interval: float = 1.5, max_entries: int = 600,
                 dedup_threshold: float = 0.82) -> None:
        self._snap = snapshots
        self._dir = Path(snap_dir)
        self._seg = seg_backend
        self._min_area = int(min_area)
        self._refresh = float(refresh_ratio)
        self._shot_interval = float(shot_interval)
        self._max = int(max_entries)
        self._dedup = float(dedup_threshold)
        self._entries: dict[str, dict] = {}
        self._counter: dict[str, int] = {"person": 0, "vehicle": 0}
        self._lock = threading.Lock()

    def _url(self, p: Path) -> str | None:
        try:
            return "/snapshots/" + Path(p).relative_to(self._dir).as_posix()
        except Exception:  # noqa: BLE001
            s = str(p).replace("\\", "/")
            return "/snapshots/" + s.split("/snapshots/")[-1] if "/snapshots/" in s else None

    def _new_id(self, cls: str) -> str:
        self._counter[cls] = self._counter.get(cls, 0) + 1
        return f"{'V' if cls == 'vehicle' else 'P'}-{self._counter[cls]:03d}"

    def observe_reid(self, cls: str, crop: Any, embedding: np.ndarray | None, now: float,
                     plate: str | None = None, attrs: dict | None = None,
                     cam: str | None = None) -> str:
        """Fold one sighting into the roster, merging with an existing entry whose appearance
        embedding is close enough (same class), else creating a new anonymous identity.
        Keeps the best (largest) crop as the photo. Returns the entry id."""
        area = int(crop.shape[0] * crop.shape[1]) if crop is not None and getattr(crop, "size", 0) else 0
        with self._lock:
            match_id: str | None = None
            if embedding is not None:
                best = self._dedup
                for eid, e in self._entries.items():
                    if e["cls"] != cls or e.get("embedding") is None:
                        continue
                    sim = _cosine(embedding, e["embedding"])
                    if sim > best:
                        best, match_id = sim, eid
            if match_id is not None:
                e = self._entries[match_id]
            else:
                if len(self._entries) >= self._max:
                    oldest = min(self._entries, key=lambda k: self._entries[k]["last_ts"])
                    self._entries.pop(oldest, None)
                eid = self._new_id(cls)
                e = {"id": eid, "cls": cls, "first_ts": now, "last_ts": now, "obs": 0,
                     "snapshot": None, "snapshot_path": None, "best_area": 0.0,
                     "plate": None, "attrs": {}, "cam": cam, "first_cam": cam,
                     "last_shot": 0.0, "embedding": embedding, "trail": {}, "clip": None}
                self._entries[eid] = e
            e["last_ts"] = now
            e["obs"] += 1
            if cam:
                e["cam"] = cam
                # per-camera sighting record: builds the subject's movement trail across cameras
                seg = e["trail"].get(cam)
                if seg is None:
                    e["trail"][cam] = {"first": now, "last": now, "count": 1}
                else:
                    seg["last"] = now
                    seg["count"] += 1
            if plate:
                e["plate"] = plate
            if attrs:
                e["attrs"] = {**e["attrs"], **{k: v for k, v in attrs.items() if v}}
            first = e["snapshot"] is None
            better = (area > e["best_area"] * self._refresh
                      and now - e["last_shot"] >= self._shot_interval)
            if area >= self._min_area and crop is not None and (first or better):
                try:
                    p = self._snap.save(crop.copy(), prefix="roster")
                    e["snapshot_path"] = str(p)
                    e["snapshot"] = self._url(p)
                    e["best_area"] = float(area)
                    e["last_shot"] = now
                    if embedding is not None:
                        e["embedding"] = embedding   # keep the best crop's embedding
                except Exception:  # noqa: BLE001
                    pass
            return e["id"]

    @staticmethod
    def _public(e: dict) -> dict:
        # the movement trail: cameras this subject was seen on, earliest sighting first
        trail = [{"cam": c, "first": s["first"] * 1000, "last": s["last"] * 1000,
                  "count": s["count"]}
                 for c, s in sorted(e.get("trail", {}).items(), key=lambda kv: kv[1]["first"])]
        return {"id": e["id"], "cls": e["cls"], "snapshot": e["snapshot"], "plate": e["plate"],
                "attrs": e["attrs"], "obs": e["obs"], "cam": e.get("cam"),
                "first_cam": e.get("first_cam"), "trail": trail, "clip": e.get("clip"),
                "first_ts": e["first_ts"] * 1000, "last_ts": e["last_ts"] * 1000}

    def list(self) -> list[dict]:
        with self._lock:
            return sorted((self._public(e) for e in self._entries.values() if e["snapshot"]),
                          key=lambda x: -x["last_ts"])

    def needs_clip(self, det_id: str) -> bool:
        """A logged subject (has a photo) that hasn't got its short sighting clip yet."""
        with self._lock:
            e = self._entries.get(det_id)
            return bool(e and e["snapshot"] and not e.get("clip"))

    def set_clip(self, det_id: str, url: str | None) -> None:
        with self._lock:
            e = self._entries.get(det_id)
            if e is not None and url:
                e["clip"] = url

    def get(self, det_id: str) -> dict | None:
        with self._lock:
            e = self._entries.get(det_id)
            return self._public(e) if e else None

    def cutout_png(self, det_id: str) -> bytes | None:
        """The entry's photo with its background removed (YOLO-seg) as a transparent PNG;
        falls back to the plain photo when segmentation is unavailable."""
        import cv2
        with self._lock:
            e = self._entries.get(det_id)
            path = e["snapshot_path"] if e else None
            cls = e["cls"] if e else ""
        if not path or not Path(path).exists():
            return None
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            return None
        mask = None
        if self._seg is not None:
            try:
                if self._seg.available():
                    mask = self._seg.mask(img, cls)
            except Exception:  # noqa: BLE001
                mask = None
        if mask is None:
            ok, buf = cv2.imencode(".png", img)
            return buf.tobytes() if ok else None
        rgba = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        rgba[:, :, 3] = np.where(np.asarray(mask).astype(bool), 255, 0).astype(np.uint8)
        ok, buf = cv2.imencode(".png", rgba)
        return buf.tobytes() if ok else None

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._counter = {"person": 0, "vehicle": 0}


class RosterHarvester(threading.Thread):
    """Continuously fills the roster from ALL cameras in the background — independent of the
    single actively-analysed camera. Round-robins the sources, grabs each one's current
    frame, detects people/vehicles with its OWN detector (so it never races the live YOLO),
    embeds each for ReID de-duplication, and folds them into the roster."""

    def __init__(self, roster: SessionRoster,
                 sources_fn: Callable[[], list], frame_fn: Callable[[Any], Any],
                 detect_fn: Callable[[Any], list], embed_fn: Callable[[Any, str], Any],
                 cat_to_cls: dict[str, str], *, plate_fn: Callable[[Any], str | None] | None = None,
                 attrs_fn: Callable[[Any, str], dict] | None = None,
                 clip_fn: Callable[[Any, tuple], str | None] | None = None,
                 interval: float = 4.0) -> None:
        super().__init__(daemon=True, name="RosterHarvester")
        self._roster = roster
        self._sources_fn = sources_fn
        self._frame_fn = frame_fn
        self._detect_fn = detect_fn
        self._embed_fn = embed_fn
        self._cat2cls = cat_to_cls
        self._plate_fn = plate_fn
        self._attrs_fn = attrs_fn
        self._clip_fn = clip_fn
        self._interval = float(interval)
        self._i = 0
        self._stopped = threading.Event()

    def _scan(self, source: Any) -> None:
        frame = self._frame_fn(source)
        if frame is None or getattr(frame, "size", 0) == 0:
            return
        fh, fw = frame.shape[:2]
        cam = getattr(source, "name", None)
        clipped = False   # at most one short sighting clip captured per scan (bounds the cost)
        for d in self._detect_fn(frame) or []:
            cls = self._cat2cls.get(getattr(d, "category", "object"), "object")
            if cls not in ("person", "vehicle"):
                continue
            x1, y1, x2, y2 = (int(v) for v in d.bbox)
            x1, y1, x2, y2 = max(0, x1), max(0, y1), min(fw, x2), min(fh, y2)
            if x2 - x1 < 24 or y2 - y1 < 24:
                continue
            crop = frame[y1:y2, x1:x2]
            if crop.size == 0:
                continue
            try:
                emb = self._embed_fn(crop, cls)
            except Exception:  # noqa: BLE001
                emb = None
            plate = None
            if cls == "vehicle" and self._plate_fn is not None:
                try:
                    plate = self._plate_fn(crop)
                except Exception:  # noqa: BLE001
                    plate = None
            attrs = None
            if self._attrs_fn is not None:
                try:
                    attrs = self._attrs_fn(crop, cls)
                except Exception:  # noqa: BLE001
                    attrs = None
            if cls == "vehicle":  # keep the fine COCO subtype (car/truck/bus/...) on the card
                subtype = getattr(d, "label", None)
                if subtype:
                    attrs = {**(attrs or {}), "subtype": subtype}
            eid = self._roster.observe_reid(cls, crop, emb, time.time(), plate=plate,
                                            attrs=attrs, cam=cam)
            # once per subject, capture a short clip of the sighting (a padded burst around it).
            # bbox is normalized so the clip burst can be a different resolution than this frame.
            if (not clipped and self._clip_fn is not None and self._roster.needs_clip(eid)):
                clipped = True
                nbbox = (x1 / fw, y1 / fh, x2 / fw, y2 / fh)
                try:
                    url = self._clip_fn(source, nbbox)
                except Exception:  # noqa: BLE001
                    url = None
                if url:
                    self._roster.set_clip(eid, url)

    def run(self) -> None:
        while not self._stopped.is_set():
            sources = []
            try:
                sources = list(self._sources_fn() or [])
            except Exception:  # noqa: BLE001
                sources = []
            if not sources:
                self._stopped.wait(self._interval)
                continue
            source = sources[self._i % len(sources)]
            self._i += 1
            try:
                self._scan(source)
            except Exception:  # noqa: BLE001 - never let one bad frame kill the harvester
                pass
            # pace so every camera is visited about once per `interval`
            self._stopped.wait(max(0.25, self._interval / len(sources)))

    def stop(self) -> None:
        self._stopped.set()
