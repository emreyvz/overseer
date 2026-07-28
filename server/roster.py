"""Session roster: an anonymous, deduplicated registry of every person and vehicle seen on
the active camera this session, with a representative photo (and, for vehicles, a plate).

Entries are keyed by the tracker id, so a continuous appearance is one entry (ByteTrack does
the identity work upstream). Each entry keeps its best — largest, so clearest — crop as the
roster photo, refreshed as a bigger view of the same track comes along, throttled so it is
cheap. A background-removed cutout of the photo is produced on demand via YOLO-seg.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any


class SessionRoster:
    def __init__(self, snapshots: Any, snap_dir: Path, seg_backend: Any = None,
                 *, min_area: int = 1400, refresh_ratio: float = 1.4,
                 shot_interval: float = 1.5, max_entries: int = 600) -> None:
        self._snap = snapshots
        self._dir = Path(snap_dir)
        self._seg = seg_backend
        self._min_area = int(min_area)
        self._refresh = float(refresh_ratio)
        self._shot_interval = float(shot_interval)
        self._max = int(max_entries)
        self._entries: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _url(self, p: Path) -> str | None:
        try:
            return "/snapshots/" + Path(p).relative_to(self._dir).as_posix()
        except Exception:  # noqa: BLE001
            s = str(p).replace("\\", "/")
            return "/snapshots/" + s.split("/snapshots/")[-1] if "/snapshots/" in s else None

    def observe(self, det_id: str, cls: str, crop: Any, now: float,
                plate: str | None = None, attrs: dict | None = None) -> None:
        """Record one sighting: refresh last-seen, plate and attrs, and keep the best photo."""
        area = int(crop.shape[0] * crop.shape[1]) if crop is not None and getattr(crop, "size", 0) else 0
        with self._lock:
            e = self._entries.get(det_id)
            if e is None:
                if len(self._entries) >= self._max:
                    oldest = min(self._entries, key=lambda k: self._entries[k]["last_ts"])
                    self._entries.pop(oldest, None)
                e = {"id": det_id, "cls": cls, "first_ts": now, "last_ts": now, "obs": 0,
                     "snapshot": None, "snapshot_path": None, "best_area": 0.0,
                     "plate": None, "attrs": {}, "last_shot": 0.0}
                self._entries[det_id] = e
            e["last_ts"] = now
            e["obs"] += 1
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
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _public(e: dict) -> dict:
        return {"id": e["id"], "cls": e["cls"], "snapshot": e["snapshot"], "plate": e["plate"],
                "attrs": e["attrs"], "obs": e["obs"],
                "first_ts": e["first_ts"] * 1000, "last_ts": e["last_ts"] * 1000}

    def list(self) -> list[dict]:
        with self._lock:
            return sorted((self._public(e) for e in self._entries.values() if e["snapshot"]),
                          key=lambda x: -x["last_ts"])

    def get(self, det_id: str) -> dict | None:
        with self._lock:
            e = self._entries.get(det_id)
            return self._public(e) if e else None

    def cutout_png(self, det_id: str) -> bytes | None:
        """The entry's photo with its background removed (YOLO-seg), as a transparent PNG.
        Falls back to the plain photo when segmentation is unavailable."""
        import cv2
        import numpy as np
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
