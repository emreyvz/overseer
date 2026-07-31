"""Session roster: an anonymous, ReID-deduplicated registry of every person and vehicle
seen across ALL cameras this session, with a representative photo (and, for vehicles, a
plate). A background harvester (RosterHarvester) periodically scans each camera's current
frame — independent of which camera is being actively analysed — detects people/vehicles,
embeds them, and folds them in here. Entries are merged by appearance embedding (cosine),
so the same subject seen again (another frame, another camera) is one anonymous identity,
not a pile of duplicates. Background cutouts use YOLO-seg.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

log = logging.getLogger("overseer.roster")


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
                 dedup_threshold: float = 0.82,
                 auto_merge: bool = True, auto_merge_threshold: float = 0.78,
                 subject_store: Any = None) -> None:
        self._snap = snapshots
        self._dir = Path(snap_dir)
        self._seg = seg_backend
        self._subjects = subject_store   # optional persistent long-term identity (features 5/6)
        self._min_area = int(min_area)
        self._refresh = float(refresh_ratio)
        self._shot_interval = float(shot_interval)
        self._max = int(max_entries)
        self._dedup = float(dedup_threshold)
        self._auto_merge = bool(auto_merge)               # fold high-confidence duplicates unattended
        self._auto_merge_th = float(auto_merge_threshold)  # cosine floor for an automatic merge
        self._entries: dict[str, dict] = {}
        self._counter: dict[str, int] = {"person": 0, "vehicle": 0}
        self._merge_rejected: set[frozenset] = set()   # pairs an operator said are NOT the same
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
                     cam: str | None = None, gait: np.ndarray | None = None) -> str:
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
                     "last_shot": 0.0, "embedding": embedding, "trail": {}, "clip": None,
                     "watched": False, "last_watch_ts": 0.0, "last_watch_cam": None}
                self._entries[eid] = e
            e["last_ts"] = now
            e["obs"] += 1
            if cam:
                e["cam"] = cam
                # per-camera sighting record: builds the subject's movement trail across cameras
                seg = e["trail"].get(cam)
                if seg is None:
                    e["trail"][cam] = {"first": now, "last": now, "count": 1, "clip": None}
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
            # persist into the long-term identity store (feature 6): recognize this subject across
            # days and log the sighting. Guarded + throttled so it can never slow or break the roster.
            if (self._subjects is not None and e.get("embedding") is not None
                    and (now - e.get("_last_persist", 0.0) >= 3.0)):
                try:
                    res = self._subjects.record(
                        cls, appearance=e["embedding"], gait=gait, now=now,
                        snapshot_path=e.get("snapshot_path"), cam=cam, plate=e.get("plate"),
                        attrs={k: v for k, v in e["attrs"].items() if not k.startswith("_")},
                        clip_path=e.get("clip"))
                    e["subject_uid"] = res.get("subject_id")
                    e["subject_flags"] = res.get("flags", [])
                    e["_last_persist"] = now
                except Exception:  # noqa: BLE001
                    pass
            return e["id"]

    @staticmethod
    def _public(e: dict) -> dict:
        # the movement trail: cameras this subject was seen on, earliest sighting first
        trail = [{"cam": c, "first": s["first"] * 1000, "last": s["last"] * 1000,
                  "count": s["count"], "clip": s.get("clip")}
                 for c, s in sorted(e.get("trail", {}).items(), key=lambda kv: kv[1]["first"])]
        return {"id": e["id"], "cls": e["cls"], "snapshot": e["snapshot"], "plate": e["plate"],
                "attrs": e["attrs"], "obs": e["obs"], "cam": e.get("cam"),
                "first_cam": e.get("first_cam"), "trail": trail, "clip": e.get("clip"),
                "watched": bool(e.get("watched")),
                # link to the persistent long-term subject (features 5/6/7) if one was recorded
                "subject_uid": e.get("subject_uid"), "subject_flags": e.get("subject_flags", []),
                "first_ts": e["first_ts"] * 1000, "last_ts": e["last_ts"] * 1000}

    def list(self) -> list[dict]:
        with self._lock:
            return sorted((self._public(e) for e in self._entries.values() if e["snapshot"]),
                          key=lambda x: -x["last_ts"])

    def needs_clip(self, det_id: str, cam: str | None = None) -> bool:
        """A logged subject that still needs a short sighting clip — the primary one when cam is
        None, else the clip for that camera's leg of the journey (feeds the journey supercut)."""
        with self._lock:
            e = self._entries.get(det_id)
            if not e or not e["snapshot"]:
                return False
            if cam is None:
                return not e.get("clip")
            seg = e["trail"].get(cam)
            return bool(seg and not seg.get("clip"))

    def set_clip(self, det_id: str, url: str | None, cam: str | None = None) -> None:
        with self._lock:
            e = self._entries.get(det_id)
            if e is None or not url:
                return
            if not e.get("clip"):
                e["clip"] = url                       # primary clip for the live wall
            if cam is not None and cam in e["trail"]:
                e["trail"][cam]["clip"] = url          # this camera's leg (for the supercut)

    def clip_paths(self, det_id: str) -> list[dict]:
        """Ordered per-camera clip URLs for a subject's journey supercut."""
        with self._lock:
            e = self._entries.get(det_id)
            if e is None:
                return []
            segs = sorted(e.get("trail", {}).items(), key=lambda kv: kv[1]["first"])
            return [{"cam": c, "clip": s["clip"], "first": s["first"] * 1000}
                    for c, s in segs if s.get("clip")]

    def watch(self, det_id: str, on: bool = True) -> dict | None:
        """Flag/unflag a subject as watched (BOLO). A watched subject fires an alert whenever
        it is re-identified on a camera. Returns the updated public entry, or None."""
        with self._lock:
            e = self._entries.get(det_id)
            if e is None:
                return None
            e["watched"] = bool(on)
            if not on:
                e["last_watch_ts"] = 0.0
                e["last_watch_cam"] = None
            return self._public(e)

    def take_watch_hit(self, det_id: str, cam: str | None, now: float,
                       cooldown: float) -> dict | None:
        """If det_id is a watched subject seen again — on a new camera, or after the cooldown
        on the same one — record and return its public entry for a BOLO alert, else None."""
        with self._lock:
            e = self._entries.get(det_id)
            if e is None or not e.get("watched"):
                return None
            if now - e.get("last_watch_ts", 0.0) < cooldown and cam == e.get("last_watch_cam"):
                return None
            e["last_watch_ts"] = now
            e["last_watch_cam"] = cam
            return self._public(e)

    def get(self, det_id: str) -> dict | None:
        with self._lock:
            e = self._entries.get(det_id)
            return self._public(e) if e else None

    def snapshot_bgr(self, det_id: str) -> tuple | None:
        """The subject's stored photo as a BGR image plus its class — the query for a
        'find this subject across cameras' ReID search."""
        import cv2
        with self._lock:
            e = self._entries.get(det_id)
            path = e["snapshot_path"] if e else None
            cls = e["cls"] if e else ""
        if not path or not Path(path).exists():
            return None
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        return (img, cls) if img is not None else None

    def merge_candidates(self, low: float = 0.62, limit: int = 40) -> list[dict]:
        """Likely-duplicate pairs for the merge center: same-class entries whose appearance
        embeddings are similar (cosine >= low, i.e. below the auto-merge threshold so they were
        NOT already folded), or that share a plate. Rejected pairs are excluded."""
        with self._lock:
            items = [(eid, e) for eid, e in self._entries.items()
                     if e["snapshot"] and e.get("embedding") is not None]
            out: list[dict] = []
            for i in range(len(items)):
                ai, ae = items[i]
                for j in range(i + 1, len(items)):
                    bi, be = items[j]
                    if ae["cls"] != be["cls"] or frozenset((ai, bi)) in self._merge_rejected:
                        continue
                    plate = bool(ae["cls"] == "vehicle" and ae["plate"] and ae["plate"] == be["plate"])
                    sim = _cosine(ae["embedding"], be["embedding"])
                    if plate or sim >= low:
                        out.append({"a": self._public(ae), "b": self._public(be),
                                    "similarity": 1.0 if plate else round(float(sim), 3),
                                    "reason": "plate" if plate else "appearance"})
            out.sort(key=lambda x: -x["similarity"])
            return out[:limit]

    def reject_merge(self, a: str, b: str) -> None:
        """Remember that two subjects are NOT the same, so they're never suggested again."""
        with self._lock:
            self._merge_rejected.add(frozenset((a, b)))

    def merge(self, keep_id: str, drop_id: str) -> dict | None:
        """Fold drop_id into keep_id as one canonical identity: union the movement trails, sum
        the sightings, keep the best photo/embedding, and carry over plate/clip/watched."""
        with self._lock:
            return self._merge_locked(keep_id, drop_id)

    def _merge_locked(self, keep_id: str, drop_id: str) -> dict | None:
        """merge() body without acquiring the lock — the caller must already hold it (so the
        auto-merge pass can fold several pairs atomically without re-entering a non-reentrant
        lock)."""
        if keep_id == drop_id:
            return None
        keep = self._entries.get(keep_id)
        drop = self._entries.get(drop_id)
        if keep is None or drop is None:
            return None
        if drop["first_ts"] < keep["first_ts"]:
            keep["first_cam"] = drop.get("first_cam")
        keep["first_ts"] = min(keep["first_ts"], drop["first_ts"])
        keep["last_ts"] = max(keep["last_ts"], drop["last_ts"])
        keep["obs"] += drop["obs"]
        for cam, seg in drop["trail"].items():
            k = keep["trail"].get(cam)
            if k is None:
                keep["trail"][cam] = seg
            else:
                k["first"] = min(k["first"], seg["first"])
                k["last"] = max(k["last"], seg["last"])
                k["count"] += seg["count"]
                k["clip"] = k.get("clip") or seg.get("clip")
        if drop["best_area"] > keep["best_area"]:   # keep the sharper representative photo
            for f in ("snapshot", "snapshot_path", "best_area", "embedding"):
                keep[f] = drop[f]
        keep["plate"] = keep["plate"] or drop["plate"]
        keep["clip"] = keep.get("clip") or drop.get("clip")
        keep["watched"] = bool(keep.get("watched") or drop.get("watched"))
        keep["attrs"] = {**drop["attrs"], **keep["attrs"]}
        del self._entries[drop_id]
        return self._public(keep)

    @staticmethod
    def _temporally_impossible(a: dict, b: dict) -> bool:
        """True if two entries can't be the same subject because they were seen on DIFFERENT
        cameras at the same time (a person can't be in two places at once). Trail times are in
        seconds; a small epsilon avoids blocking on near-adjacent, non-overlapping legs."""
        eps = 1.0
        for cam_a, sa in a["trail"].items():
            for cam_b, sb in b["trail"].items():
                if cam_a != cam_b and sa["first"] < sb["last"] - eps and sb["first"] < sa["last"] - eps:
                    return True
        return False

    def auto_merge_pass(self) -> list[tuple[str, str, float]]:
        """Fold the very-confident duplicate pairs automatically (Balanced policy): same plate,
        or appearance cosine >= the auto-merge threshold. Skips pairs an operator marked NOT SAME
        and pairs that are temporally impossible. Greedy by similarity so one entry isn't folded
        twice in a pass. Returns (keep, drop, similarity) for each merge, and logs them."""
        if not self._auto_merge:
            return []
        merged: list[tuple[str, str, float]] = []
        with self._lock:
            items = [(eid, e) for eid, e in self._entries.items()
                     if e["snapshot"] and e.get("embedding") is not None]
            cands: list[tuple[float, str, str]] = []
            for i in range(len(items)):
                ai, ae = items[i]
                for j in range(i + 1, len(items)):
                    bi, be = items[j]
                    if ae["cls"] != be["cls"] or frozenset((ai, bi)) in self._merge_rejected:
                        continue
                    plate = bool(ae["cls"] == "vehicle" and ae["plate"] and ae["plate"] == be["plate"])
                    sim = 1.0 if plate else _cosine(ae["embedding"], be["embedding"])
                    if not plate and sim < self._auto_merge_th:
                        continue
                    if self._temporally_impossible(ae, be):
                        continue
                    cands.append((float(sim), ai, bi))
            cands.sort(reverse=True)
            dropped: set[str] = set()
            for sim, ai, bi in cands:
                if ai in dropped or bi in dropped:
                    continue
                a, b = self._entries.get(ai), self._entries.get(bi)
                if a is None or b is None:
                    continue
                keep_id, drop_id = (ai, bi) if a["obs"] >= b["obs"] else (bi, ai)
                if self._merge_locked(keep_id, drop_id) is not None:
                    dropped.add(drop_id)
                    merged.append((keep_id, drop_id, round(sim, 3)))
        for keep_id, drop_id, sim in merged:
            log.info("roster auto-merge: %s ⇐ %s (%.2f)", keep_id, drop_id, sim)
        return merged

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
                 watch_hit_fn: Callable[[dict], None] | None = None,
                 plate_hit_fn: Callable[[str, Any], None] | None = None,
                 relate_fn: Callable[[list, Any, float], None] | None = None,
                 profile_fn: Callable[[object, float, list], None] | None = None,
                 watch_cooldown: float = 45.0,
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
        self._watch_hit_fn = watch_hit_fn
        self._plate_hit_fn = plate_hit_fn
        self._relate_fn = relate_fn
        self._profile_fn = profile_fn
        self._watch_cooldown = float(watch_cooldown)
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
        frame_ids: list[str] = []   # subjects co-present in this frame -> relationship graph
        dets_raw = self._detect_fn(frame) or []
        # Feed the camera profile (DNA + reputation) so EVERY camera — not just the actively
        # analysed one — builds a behavioural fingerprint over time. Motion isn't available in the
        # passive sweep (no consecutive frames), and fps is the camera's, not our slow scan rate,
        # so we pass neutral values and rely on brightness + the detection mix for the tags.
        if self._profile_fn is not None:
            try:
                brightness = float(frame.mean())
                prof_dets = [(self._cat2cls.get(getattr(d, "category", "object"), "object"),
                              float(getattr(d, "confidence", 0.0))) for d in dets_raw]
                self._profile_fn(getattr(source, "id", None), brightness, prof_dets)
            except Exception:  # noqa: BLE001
                pass
        for d in dets_raw:
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
                if plate and self._plate_hit_fn is not None:   # plate watchlist across all cameras
                    try:
                        self._plate_hit_fn(plate, cam)
                    except Exception:  # noqa: BLE001
                        pass
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
            frame_ids.append(eid)
            # BOLO: a watched subject re-identified here fires an alert (cooldown-gated)
            if self._watch_hit_fn is not None:
                hit = self._roster.take_watch_hit(eid, cam, time.time(), self._watch_cooldown)
                if hit is not None:
                    try:
                        self._watch_hit_fn(hit)
                    except Exception:  # noqa: BLE001
                        pass
            # capture a short clip for this subject on THIS camera the first time — one per scan,
            # so a subject's journey across cameras builds up a clip per leg (for the supercut).
            # bbox is normalized so the clip burst can be a different resolution than this frame.
            if (not clipped and self._clip_fn is not None and self._roster.needs_clip(eid, cam)):
                clipped = True
                nbbox = (x1 / fw, y1 / fh, x2 / fw, y2 / fh)
                try:
                    url = self._clip_fn(source, nbbox)
                except Exception:  # noqa: BLE001
                    url = None
                if url:
                    self._roster.set_clip(eid, url, cam=cam)
        # subjects co-present in this frame become associates in the relationship graph
        if self._relate_fn is not None and len(frame_ids) >= 2:
            try:
                self._relate_fn(frame_ids, cam, time.time())
            except Exception:  # noqa: BLE001
                pass

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
            # after each full sweep of every camera, fold the confident duplicate pairs
            if self._i % len(sources) == 0:
                try:
                    self._roster.auto_merge_pass()
                except Exception:  # noqa: BLE001 - auto-merge must never kill the harvester
                    pass
            # pace so every camera is visited about once per `interval`
            self._stopped.wait(max(0.25, self._interval / len(sources)))

    def stop(self) -> None:
        self._stopped.set()
