"""Long-term identity: recognize the same person/vehicle across days and weeks.

The live session roster (server/roster.py) dedups identities within one run but is RAM-only, so a
person who leaves and returns tomorrow is a brand-new stranger. This store gives identity a durable
backbone: a persistent gallery of many descriptors per subject (appearance embeddings + gait/soft
biometrics), matched with a fused cosine so recognition survives clothing changes and face
occlusion. Every sighting is logged, which turns into the repeat-visitor dossier (first/last seen,
visit count, per-camera and time-of-day patterns) and a "casing/repeat visitor" flag.

Pure orchestration over `storage.database.Database` + numpy, so it unit-tests against a temp DB
with no live pipeline.
"""
from __future__ import annotations

import time

import numpy as np

APPEARANCE = "appearance"
GAIT = "gait"


def _day_str(ts: float) -> str:
    # UTC, to match SQLite date(ts,'unixepoch') used for distinct-day counts
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def _norm(v: np.ndarray) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32).ravel()
    n = float(np.linalg.norm(v))
    return v / n if n > 1e-6 else v


class SubjectStore:
    """Match new sightings against a persistent identity gallery and record them.

    Thresholds are cosine similarities in [0,1]. Appearance-only matches need `appearance_threshold`;
    when a gait descriptor is present on both sides the fused score must clear `fused_threshold`.
    """

    def __init__(self, db, *, appearance_threshold: float = 0.74, fused_threshold: float = 0.70,
                 w_appearance: float = 0.7, w_gait: float = 0.3, recent_window_s: float = 21 * 86400,
                 descriptor_interval_s: float = 20.0, sighting_interval_s: float = 8.0,
                 repeat_visitor_days: int = 3, cache_ttl_s: float = 20.0) -> None:
        self.db = db
        self.app_thr = appearance_threshold
        self.fused_thr = fused_threshold
        self.w_app = w_appearance
        self.w_gait = w_gait
        self.recent_window_s = recent_window_s
        self.desc_interval = descriptor_interval_s
        self.sight_interval = sighting_interval_s
        self.repeat_days = repeat_visitor_days
        self.cache_ttl = cache_ttl_s
        # in-memory gallery cache: cls -> {subject_id: {"app": (K,D) matrix, "gait": (K,G) matrix|None}}
        self._gallery: dict[str, dict[int, dict]] = {}
        self._built_at: dict[str, float] = {}
        self._last_desc: dict[int, float] = {}     # subject_id -> last descriptor write ts (throttle)
        self._last_sight: dict[int, float] = {}    # subject_id -> last sighting write ts (throttle)

    # -- gallery cache ------------------------------------------------------
    def _rebuild(self, cls: str, now: float) -> None:
        gal: dict[int, dict] = {}
        for kind in (APPEARANCE, GAIT):
            for sid, blob, dim in self.db.subject_descriptors(
                    cls, kind, since=now - self.recent_window_s):
                vec = np.frombuffer(blob, dtype=np.float32)
                if vec.size != dim:
                    continue
                gal.setdefault(sid, {}).setdefault(kind, []).append(vec)
        for sid, kinds in gal.items():
            for kind, rows in list(kinds.items()):
                kinds[kind] = np.stack(rows, axis=0) if rows else None
        self._gallery[cls] = gal
        self._built_at[cls] = now

    def _gallery_for(self, cls: str, now: float) -> dict[int, dict]:
        if now - self._built_at.get(cls, 0.0) > self.cache_ttl:
            self._rebuild(cls, now)
        return self._gallery.get(cls, {})

    def _cache_append(self, cls: str, sid: int, kind: str, vec: np.ndarray) -> None:
        """Keep the warm cache correct between rebuilds so a subject created seconds ago is matchable
        immediately (else the same person would spawn a duplicate until the next TTL rebuild)."""
        gal = self._gallery.setdefault(cls, {})
        entry = gal.setdefault(sid, {})
        cur = entry.get(kind)
        entry[kind] = vec[None, :] if cur is None else np.vstack([cur, vec])[-12:]

    # -- matching -----------------------------------------------------------
    def _match(self, cls: str, app: np.ndarray, gait: np.ndarray | None,
               now: float) -> tuple[int | None, float]:
        gal = self._gallery_for(cls, now)
        best_id, best_score = None, -1.0
        for sid, kinds in gal.items():
            am = kinds.get(APPEARANCE)
            if am is None:
                continue
            app_sim = float(np.max(am @ app))
            if gait is not None and kinds.get(GAIT) is not None:
                gait_sim = float(np.max(kinds[GAIT] @ gait))
                score = self.w_app * app_sim + self.w_gait * gait_sim
                thr = self.fused_thr
            else:
                score, thr = app_sim, self.app_thr
            if score > best_score and score >= thr:
                best_id, best_score = sid, score
        return best_id, best_score

    # -- recording ----------------------------------------------------------
    def record(self, cls: str, *, appearance, gait=None, now: float | None = None,
               snapshot_path: str | None = None, cam: str | None = None,
               source_id: int | None = None, plate: str | None = None,
               attrs: dict | None = None, clip_path: str | None = None) -> dict:
        """Match a sighting to a persisted subject (or create one), log it, and update its gallery.

        Returns {subject_id, is_new, score, flags}.
        """
        now = time.time() if now is None else now
        app = _norm(appearance)
        gvec = _norm(gait) if gait is not None else None
        day = _day_str(now)

        sid, score = self._match(cls, app, gvec, now)
        is_new = sid is None
        if is_new:
            sid = self.db.add_subject(cls, now, day=day, plate=plate, attrs=attrs,
                                      snapshot_path=snapshot_path)
        else:
            self.db.touch_subject(sid, now, day=day, snapshot_path=snapshot_path,
                                  plate=plate, attrs=attrs)

        # throttle the append-only logs so a subject lingering in frame doesn't flood the DB
        if now - self._last_sight.get(sid, 0.0) >= self.sight_interval or is_new:
            self.db.add_sighting(sid, now, source_id=source_id, cam=cam,
                                 snapshot_path=snapshot_path, clip_path=clip_path)
            self._last_sight[sid] = now
        if now - self._last_desc.get(sid, 0.0) >= self.desc_interval or is_new:
            self.db.add_subject_descriptor(sid, APPEARANCE, app.tobytes(), int(app.size),
                                           "reid", now)
            self._cache_append(cls, sid, APPEARANCE, app)
            if gvec is not None:
                self.db.add_subject_descriptor(sid, GAIT, gvec.tobytes(), int(gvec.size), "gait", now)
                self._cache_append(cls, sid, GAIT, gvec)
            self._last_desc[sid] = now

        flags = self._update_flags(sid, now)
        return {"subject_id": sid, "is_new": is_new, "score": round(max(0.0, score), 4), "flags": flags}

    def _update_flags(self, sid: int, now: float) -> list[str]:
        subj = self.db.get_subject(sid)
        if subj is None:
            return []
        flags = set(subj.get("flags") or [])
        if subj["day_count"] >= self.repeat_days:
            flags.add("repeat_visitor")
        if set(flags) != set(subj.get("flags") or []):
            self.db.set_subject_flags(sid, list(flags), now)
        return sorted(flags)

    # -- reads (dossier / list) --------------------------------------------
    def dossier(self, subject_id: int) -> dict | None:
        d = self.db.subject_dossier(subject_id)
        if d is None:
            return None
        d["sightings"] = self.db.list_sightings(subject_id, limit=500)
        return d

    def list(self, *, cls: str | None = None, limit: int = 200, order: str = "last_seen") -> list[dict]:
        return self.db.list_subjects(cls=cls, limit=limit, order=order)
