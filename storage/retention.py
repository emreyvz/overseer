"""Retention policy: prune old recordings/events/snapshots by age and total size."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from core.config import Config
from storage.database import Database


@dataclass(frozen=True)
class RetentionSummary:
    recordings_deleted: int
    events_deleted: int
    snapshots_deleted: int
    bytes_freed: int
    tracklets_deleted: int = 0


class RetentionPolicy:
    def __init__(self, config: Config, db: Database, snapshot_dir: Path) -> None:
        self._enabled = bool(config.get("retention.enabled", True))
        self._max_age_days = float(config.get("retention.max_age_days", 30.0))
        self._max_total_bytes = float(config.get("retention.max_total_gb", 20.0)) * 1e9
        self._tracklet_retention_days = float(
            config.get("forensic.retention_days", 7.0))
        self._db = db
        self._snapshot_dir = snapshot_dir

    def run_once(self, now: float) -> RetentionSummary:
        # Forensic tracklet TTL is independent of media retention.enabled and of
        # session-only mode (forensic.retention_days == 0, purged on shutdown).
        tracklets_deleted = self._prune_tracklets(now)
        if not self._enabled:
            return RetentionSummary(0, 0, 0, 0, tracklets_deleted)
        cutoff = now - self._max_age_days * 86400.0
        recs_deleted = 0
        bytes_freed = 0
        for rec in self._db.list_recordings_older_than(cutoff):
            freed = self._delete_file(Path(rec.path))
            self._db.delete_recording(rec.id)
            recs_deleted += 1
            bytes_freed += freed
        events_deleted = self._db.delete_events_older_than(cutoff)
        snaps_deleted = self._prune_old_snapshots(cutoff)

        # Size-based: delete oldest recordings until total <= cap.
        if self._db.total_recordings_size() > self._max_total_bytes:
            for rec in self._db.list_recordings_older_than(now):  # all, oldest-first
                if self._db.total_recordings_size() <= self._max_total_bytes:
                    break
                bytes_freed += self._delete_file(Path(rec.path))
                self._db.delete_recording(rec.id)
                recs_deleted += 1
        return RetentionSummary(recs_deleted, events_deleted, snaps_deleted,
                                bytes_freed, tracklets_deleted)

    def _prune_tracklets(self, now: float) -> int:
        # retention_days <= 0 = session-only: no periodic prune here (it would
        # delete still-active tracklets mid-session); purge_session_tracklets()
        # runs on shutdown instead.
        if self._tracklet_retention_days <= 0:
            return 0
        cutoff = now - self._tracklet_retention_days * 86400.0
        # Read paths before deleting rows. Narrow accepted TOCTOU: a concurrent
        # set_tracklet_pinned() landing between this read and the row delete could
        # still unlink a crop for a tracklet just pinned; low-probability, accepted.
        for path in self._db.tracklet_snapshots_to_prune(cutoff):
            self._delete_file(Path(path))
        return self._db.prune_tracklets_older_than(cutoff)

    def purge_session_tracklets(self) -> int:
        # Session-only mode (forensic.retention_days == 0): delete ALL non-pinned
        # tracklets and their crops. Called on shutdown.
        if self._tracklet_retention_days > 0:
            return 0
        for path in self._db.unpinned_tracklet_snapshots():
            self._delete_file(Path(path))
        return self._db.purge_unpinned_tracklets()

    def _delete_file(self, path: Path) -> int:
        try:
            size = path.stat().st_size if path.exists() else 0
            if path.exists():
                path.unlink()
            return size
        except OSError:
            logger.exception("failed to delete {}", path)
            return 0

    def _prune_old_snapshots(self, cutoff: float) -> int:
        if not self._snapshot_dir.exists():
            return 0
        # Crops of pinned (case-held) tracklets are exempt from the generic
        # mtime sweep, even past retention.max_age_days.
        protected = {Path(p).resolve() for p in self._db.pinned_tracklet_snapshots()}
        deleted = 0
        for file in self._snapshot_dir.rglob("*.jpg"):
            try:
                if file.resolve() in protected:
                    continue
                if file.stat().st_mtime < cutoff:
                    file.unlink()
                    deleted += 1
            except OSError:
                logger.exception("failed to delete snapshot {}", file)
        return deleted
