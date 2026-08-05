"""SQLite persistence: sources, events, settings. Thread-safe via single connection + lock."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from alerts.model import Alert, AlertRule
from events.types import Event

# Public demo cameras seeded on a fresh install: (name, url, latitude, longitude).
# Placed near where each stream actually is, so the map opens with pins on four
# continents' worth of scenery instead of a single dot.
DEMO_SOURCES: tuple[tuple[str, str, float, float], ...] = (
    ("Street", "http://renzo.dyndns.tv/mjpg/video.mjpg", 58.030084, 5.892758),          # Stavanger, NO
    ("Airport", "http://199.104.253.4/mjpg/video.mjpg", 34.654500, -112.419600),        # Prescott, AZ, US
    ("Hotel", "http://85.196.146.82:3337/axis-cgi/mjpg/video.cgi", 42.682600, 23.322300),  # Sofia, BG
    ("Dock", "http://eyc.synology.me:10001/mjpg/video.mjpg", 41.648900, -70.481200),    # Mashpee, MA, US
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_used_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    type TEXT NOT NULL,
    source_id INTEGER,
    label TEXT NOT NULL,
    confidence REAL,
    bbox TEXT,
    snapshot_path TEXT,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recordings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    path TEXT NOT NULL,
    start_ts REAL NOT NULL,
    end_ts REAL,
    mode TEXT NOT NULL,
    trigger TEXT,
    source_id INTEGER,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_recordings_ts ON recordings(start_ts DESC);
CREATE TABLE IF NOT EXISTS statistics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day_start REAL NOT NULL,
    source_id INTEGER NOT NULL DEFAULT 0,
    event_type TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    UNIQUE (day_start, source_id, event_type)
);
CREATE INDEX IF NOT EXISTS idx_statistics_day ON statistics(day_start);
CREATE TABLE IF NOT EXISTS tracklets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    track_id INTEGER,
    first_ts REAL NOT NULL,
    last_ts REAL NOT NULL,
    obs_count INTEGER NOT NULL DEFAULT 0,
    snapshot_path TEXT,
    height_band TEXT,
    build TEXT,
    upper_color TEXT,
    lower_color TEXT,
    clothing_type TEXT,
    accessories TEXT NOT NULL DEFAULT '[]',
    attr_conf REAL,
    pinned INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (source_id, track_id, first_ts)
);
CREATE INDEX IF NOT EXISTS idx_tracklets_source_last ON tracklets(source_id, last_ts DESC);
CREATE INDEX IF NOT EXISTS idx_tracklets_colors ON tracklets(upper_color, lower_color);
CREATE INDEX IF NOT EXISTS idx_tracklets_created ON tracklets(created_at);
CREATE INDEX IF NOT EXISTS idx_tracklets_pinned ON tracklets(pinned);
CREATE TABLE IF NOT EXISTS tracklet_embeddings (
    tracklet_id INTEGER PRIMARY KEY REFERENCES tracklets(id) ON DELETE CASCADE,
    dim INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    vector BLOB NOT NULL,
    created_at REAL NOT NULL
);
-- Long-term cross-session identity: a durable "subject" recognized across days/weeks (feature 6),
-- its multi-descriptor gallery (appearance + gait, feature 5), and an append-only sighting log.
CREATE TABLE IF NOT EXISTS subjects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cls TEXT NOT NULL,
    label TEXT,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    sighting_count INTEGER NOT NULL DEFAULT 0,
    day_count INTEGER NOT NULL DEFAULT 1,
    last_day TEXT,
    plate TEXT,
    attrs TEXT NOT NULL DEFAULT '{}',
    snapshot_path TEXT,
    watched INTEGER NOT NULL DEFAULT 0,
    flags TEXT NOT NULL DEFAULT '[]',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subjects_cls_last ON subjects(cls, last_seen DESC);
CREATE TABLE IF NOT EXISTS subject_descriptors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    dim INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    vector BLOB NOT NULL,
    quality REAL NOT NULL DEFAULT 1.0,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subject_desc ON subject_descriptors(subject_id, kind, ts DESC);
CREATE TABLE IF NOT EXISTS sightings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id INTEGER NOT NULL REFERENCES subjects(id) ON DELETE CASCADE,
    source_id INTEGER,
    cam TEXT,
    ts REAL NOT NULL,
    snapshot_path TEXT,
    clip_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_sightings_subject_ts ON sightings(subject_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_sightings_ts ON sightings(ts DESC);
CREATE TABLE IF NOT EXISTS zones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    polygon TEXT NOT NULL,
    loiter_seconds REAL,
    allowed_direction TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_zones_source ON zones(source_id);
CREATE TABLE IF NOT EXISTS alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source_id INTEGER,
    zone_id INTEGER,
    min_count INTEGER,
    min_duration_s REAL,
    min_confidence REAL,
    severity TEXT NOT NULL DEFAULT 'warning',
    cooldown_s REAL NOT NULL DEFAULT 60,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    rule_id INTEGER,
    rule_name TEXT NOT NULL,
    event_type TEXT NOT NULL,
    source_id INTEGER,
    severity TEXT NOT NULL,
    summary TEXT NOT NULL,
    snapshot_path TEXT,
    clip_path TEXT,
    acknowledged INTEGER NOT NULL DEFAULT 0,
    metadata TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts DESC);
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    threat_level TEXT NOT NULL DEFAULT 'low',
    notes TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS case_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    tracklet_id INTEGER NOT NULL,
    alias TEXT NOT NULL DEFAULT '',
    added_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_targets_case ON case_targets(case_id);
CREATE TABLE IF NOT EXISTS case_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    kind TEXT NOT NULL DEFAULT 'event',
    event_type TEXT NOT NULL DEFAULT '',
    cam TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL DEFAULT 'info',
    summary TEXT NOT NULL DEFAULT '',
    snapshot TEXT,
    clip TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_case_events_case ON case_events(case_id);

-- ── PERCEPTION SUITE ──────────────────────────────────────────────────────────────────────
-- DREAMSTATE: the learned expectation field (statistics only, never imagery) and the
-- divergences it fired. `stats` is a packed float32 P2 quantile-estimator state per feature,
-- so the store is constant-size per (camera, time bucket, cell) no matter how long it runs.
CREATE TABLE IF NOT EXISTS dream_state (
    source_id INTEGER NOT NULL,
    bucket INTEGER NOT NULL,
    cell INTEGER NOT NULL,
    stats BLOB NOT NULL,
    n INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    PRIMARY KEY (source_id, bucket, cell)
);
CREATE TABLE IF NOT EXISTS dream_divergence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    ts REAL NOT NULL,
    peak_sigma REAL NOT NULL,
    area_sigma_s REAL NOT NULL DEFAULT 0,
    blob TEXT NOT NULL DEFAULT '[]',
    cells TEXT NOT NULL DEFAULT '[]',
    snapshot_path TEXT,
    verdict TEXT,
    verdict_ts REAL,
    triage TEXT,
    tier TEXT NOT NULL DEFAULT 'A'
);
CREATE INDEX IF NOT EXISTS idx_dream_div_ts ON dream_divergence(source_id, ts DESC);
CREATE TABLE IF NOT EXISTS dream_pulse (
    source_id INTEGER NOT NULL,
    minute_ts REAL NOT NULL,
    peak REAL NOT NULL,
    mean REAL NOT NULL,
    PRIMARY KEY (source_id, minute_ts)
);
CREATE TABLE IF NOT EXISTS dream_mute (
    source_id INTEGER NOT NULL,
    cell INTEGER NOT NULL,
    from_hour INTEGER NOT NULL DEFAULT 0,
    to_hour INTEGER NOT NULL DEFAULT 24,
    created_at REAL NOT NULL,
    PRIMARY KEY (source_id, cell)
);

-- FOG OF WAR: per-cell observability accumulation and the blind spots derived from it.
-- n_die / n_born are the EMPIRICAL channel: where tracks actually end and begin away from the
-- frame border is the operational definition of a blind spot.
CREATE TABLE IF NOT EXISTS coverage_cells (
    source_id INTEGER NOT NULL,
    cell INTEGER NOT NULL,
    n_enter INTEGER NOT NULL DEFAULT 0,
    n_die INTEGER NOT NULL DEFAULT 0,
    n_born INTEGER NOT NULL DEFAULT 0,
    occ_persist REAL NOT NULL DEFAULT 0,
    occ_samples INTEGER NOT NULL DEFAULT 0,
    quality REAL,
    px_per_m REAL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (source_id, cell)
);
CREATE TABLE IF NOT EXISTS blind_spots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    polygon TEXT NOT NULL,
    area_m2 REAL,
    persistent INTEGER NOT NULL DEFAULT 0,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    events INTEGER NOT NULL DEFAULT 0,
    channels TEXT NOT NULL DEFAULT '{}',
    dismissed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_blind_source ON blind_spots(source_id, persistent DESC, last_seen DESC);

-- GRAIN: the learned per-cell movement distributions, and every scored track.
-- `path` is the resampled trajectory, kept so the WHY card can retrieve precedents by shape.
CREATE TABLE IF NOT EXISTS grain_cell (
    source_id INTEGER NOT NULL,
    cell INTEGER NOT NULL,
    bucket INTEGER NOT NULL,
    density INTEGER NOT NULL,
    cls TEXT NOT NULL DEFAULT 'person',
    n INTEGER NOT NULL DEFAULT 0,
    heading BLOB,
    speed BLOB,
    trans BLOB,
    dwell BLOB,
    updated_at REAL NOT NULL,
    PRIMARY KEY (source_id, cell, bucket, density, cls)
);
CREATE TABLE IF NOT EXISTS grain_track (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    det_id TEXT NOT NULL,
    cls TEXT NOT NULL DEFAULT 'person',
    start_ts REAL NOT NULL,
    end_ts REAL NOT NULL,
    percentile REAL NOT NULL,
    score REAL NOT NULL DEFAULT 0,
    factors TEXT NOT NULL DEFAULT '{}',
    why TEXT,
    path BLOB NOT NULL,
    state TEXT NOT NULL DEFAULT 'unjudged',
    verdict TEXT,
    verdict_ts REAL,
    snapshot_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_grain_track ON grain_track(source_id, start_ts DESC);
CREATE INDEX IF NOT EXISTS idx_grain_pct ON grain_track(percentile);

-- EARDRUM: listening probes, their frozen baseline spectrum, and a per-minute trend row.
CREATE TABLE IF NOT EXISTS probes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    roi TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'probe',
    axis TEXT,
    texture REAL NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_probes_source ON probes(source_id);
CREATE TABLE IF NOT EXISTS probe_baseline (
    probe_id INTEGER PRIMARY KEY REFERENCES probes(id) ON DELETE CASCADE,
    psd BLOB NOT NULL,
    freqs BLOB NOT NULL,
    peaks TEXT NOT NULL DEFAULT '[]',
    floor REAL NOT NULL DEFAULT 0,
    band TEXT NOT NULL DEFAULT 'structural',
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS probe_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    probe_id INTEGER NOT NULL REFERENCES probes(id) ON DELETE CASCADE,
    ts REAL NOT NULL,
    rms REAL NOT NULL,
    snr REAL NOT NULL DEFAULT 0,
    peaks TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_probe_hist ON probe_history(probe_id, ts DESC);

-- BEDROCK: a bitemporal fact store PROJECTED from the tables above — never the source of
-- truth, always derived. valid_from/valid_to = when it was true in the world.
-- tx_from/tx_to = when we came to believe it. A correction closes tx and inserts; nothing is
-- ever deleted, which is what makes "as believed on <date>" answerable.
CREATE TABLE IF NOT EXISTS bd_entity (
    uid INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    label TEXT,
    snapshot_path TEXT,
    first_seen REAL NOT NULL,
    last_seen REAL NOT NULL,
    UNIQUE (kind, ref)
);
CREATE INDEX IF NOT EXISTS idx_bd_entity_kind ON bd_entity(kind, last_seen DESC);
CREATE TABLE IF NOT EXISTS bd_fact (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subj INTEGER NOT NULL REFERENCES bd_entity(uid) ON DELETE CASCADE,
    pred TEXT NOT NULL,
    obj INTEGER REFERENCES bd_entity(uid) ON DELETE SET NULL,
    val TEXT,
    num REAL,
    valid_from REAL NOT NULL,
    valid_to REAL,
    tx_from REAL NOT NULL,
    tx_to REAL,
    conf REAL NOT NULL DEFAULT 1.0,
    src_kind TEXT NOT NULL DEFAULT 'detector',
    src_ref TEXT,
    model_id TEXT,
    snapshot_path TEXT,
    superseded_by INTEGER
);
CREATE INDEX IF NOT EXISTS idx_bd_pred ON bd_fact(pred, valid_from DESC);
CREATE INDEX IF NOT EXISTS idx_bd_subj ON bd_fact(subj, valid_from DESC);
CREATE INDEX IF NOT EXISTS idx_bd_obj ON bd_fact(obj, valid_from DESC);
CREATE INDEX IF NOT EXISTS idx_bd_valid ON bd_fact(valid_from DESC);
"""


@dataclass(frozen=True)
class Source:
    id: int
    name: str
    url: str
    created_at: float
    last_used_at: float | None
    map_x: float | None = None
    map_y: float | None = None


@dataclass(frozen=True)
class StoredEvent:
    id: int
    timestamp: float
    type: str
    source_id: int | None
    label: str
    confidence: float | None
    bbox: tuple[int, int, int, int] | None
    snapshot_path: str | None
    metadata: dict


@dataclass(frozen=True)
class Recording:
    id: int
    kind: str
    path: str
    start_ts: float
    end_ts: float | None
    mode: str
    trigger: str | None
    source_id: int | None
    size_bytes: int
    created_at: float


@dataclass(frozen=True)
class DailyStat:
    day_start: float
    source_id: int
    event_type: str
    count: int
    updated_at: float


@dataclass(frozen=True)
class Tracklet:
    id: int
    source_id: int | None
    track_id: int | None
    first_ts: float
    last_ts: float
    obs_count: int
    snapshot_path: str | None
    height_band: str | None
    build: str | None
    upper_color: str | None
    lower_color: str | None
    clothing_type: str | None
    accessories: list
    attr_conf: float | None
    pinned: int
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class Zone:
    id: int
    source_id: int | None
    name: str
    type: str
    polygon: list
    loiter_seconds: float | None
    created_at: float
    allowed_direction: str | None = None


@dataclass(frozen=True)
class Case:
    id: int
    name: str
    threat_level: str
    notes: str
    created_at: float
    status: str = "open"


@dataclass(frozen=True)
class CaseEvent:
    id: int
    case_id: int
    ts: float
    kind: str
    event_type: str
    cam: str
    severity: str
    summary: str
    snapshot: str | None
    clip: str | None
    created_at: float


@dataclass(frozen=True)
class CaseTarget:
    id: int
    case_id: int
    tracklet_id: int
    alias: str
    added_at: float


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        cols = {r[1] for r in
                self._conn.execute("PRAGMA table_info(zones)").fetchall()}
        if "allowed_direction" not in cols:
            self._conn.execute(
                "ALTER TABLE zones ADD COLUMN allowed_direction TEXT")
        source_cols = {r[1] for r in
                       self._conn.execute("PRAGMA table_info(sources)").fetchall()}
        if "map_x" not in source_cols:
            self._conn.execute("ALTER TABLE sources ADD COLUMN map_x REAL")
        if "map_y" not in source_cols:
            self._conn.execute("ALTER TABLE sources ADD COLUMN map_y REAL")
        case_cols = {r[1] for r in
                     self._conn.execute("PRAGMA table_info(cases)").fetchall()}
        if "status" not in case_cols:  # investigation lifecycle
            self._conn.execute("ALTER TABLE cases ADD COLUMN status TEXT NOT NULL DEFAULT 'open'")
        alert_cols = {r[1] for r in
                      self._conn.execute("PRAGMA table_info(alerts)").fetchall()}
        if "clip_path" not in alert_cols:  # persist the alert's video clip so history can replay it
            self._conn.execute("ALTER TABLE alerts ADD COLUMN clip_path TEXT")

    # -- generic locked access ------------------------------------------------
    # The perception engines (dreamstate / coverage / grain / eardrum / bedrock) each own a
    # handful of tables and a lot of bespoke SQL. Rather than growing this class by another
    # forty near-identical wrappers, they compose over these two helpers — which still take the
    # same lock and honour the same WAL/commit discipline as every method above.
    def query(self, sql: str, params: "tuple | list" = ()) -> list[tuple]:
        """Run a SELECT under the connection lock and return plain tuples."""
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def execute(self, sql: str, params: "tuple | list" = (), *, commit: bool = True) -> int:
        """Run one write statement. Returns lastrowid (0 when the statement inserts nothing)."""
        with self._lock:
            cur = self._conn.execute(sql, params)
            if commit:
                self._conn.commit()
            return int(cur.lastrowid or 0)

    def execute_many(self, sql: str, rows: "list[tuple]", *, commit: bool = True) -> None:
        """Batch write — used by the fact projector and the field-statistics flushers."""
        if not rows:
            return
        with self._lock:
            self._conn.executemany(sql, rows)
            if commit:
                self._conn.commit()

    # -- sources -------------------------------------------------------------
    def add_source(self, name: str, url: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sources (name, url, created_at) VALUES (?, ?, ?)",
                (name, url, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_sources(self) -> list[Source]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, url, created_at, last_used_at, map_x, map_y"
                " FROM sources ORDER BY id"
            ).fetchall()
        return [Source(*row) for row in rows]

    def update_source(self, source_id: int, name: str, url: str,
                      map_x: float | None = None, map_y: float | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sources SET name = ?, url = ?, map_x = ?, map_y = ? WHERE id = ?",
                (name, url, map_x, map_y, source_id),
            )
            self._conn.commit()

    def set_source_position(self, source_id: int, map_x: float, map_y: float) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sources SET map_x = ?, map_y = ? WHERE id = ?",
                (map_x, map_y, source_id),
            )
            self._conn.commit()

    def delete_source(self, source_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            self._conn.commit()

    def touch_source(self, source_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sources SET last_used_at = ? WHERE id = ?", (time.time(), source_id)
            )
            self._conn.commit()

    # -- events --------------------------------------------------------------
    def add_event(self, event: Event, snapshot_path: str | None = None) -> int:
        bbox = json.dumps(list(event.bbox)) if event.bbox is not None else None
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (ts, type, source_id, label, confidence, bbox,"
                " snapshot_path, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (event.timestamp, event.type.name, event.source_id, event.label,
                 event.confidence, bbox, snapshot_path, json.dumps(event.metadata)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_events(self, limit: int = 200, since: float | None = None) -> list[StoredEvent]:
        query = ("SELECT id, ts, type, source_id, label, confidence, bbox, snapshot_path,"
                 " metadata FROM events")
        params: list[object] = []
        if since is not None:
            query += " WHERE ts >= ?"
            params.append(since)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        result: list[StoredEvent] = []
        for row in rows:
            bbox = tuple(json.loads(row[6])) if row[6] else None
            result.append(StoredEvent(
                id=row[0], timestamp=row[1], type=row[2], source_id=row[3], label=row[4],
                confidence=row[5], bbox=bbox, snapshot_path=row[7],
                metadata=json.loads(row[8]),
            ))
        return result

    def count_events_since(self, ts: float) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) FROM events WHERE ts >= ?", (ts,)
            ).fetchone()
        return int(row[0])

    def delete_events_older_than(self, ts: float) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM events WHERE ts < ?", (ts,))
            self._conn.commit()
            return int(cur.rowcount)

    # -- settings ------------------------------------------------------------
    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
        return row[0] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    # -- recordings ----------------------------------------------------------
    def add_recording(
        self, kind: str, path: str, start_ts: float, end_ts: float | None,
        mode: str, trigger: str | None, source_id: int | None, size_bytes: int,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO recordings (kind, path, start_ts, end_ts, mode, trigger,"
                " source_id, size_bytes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (kind, path, start_ts, end_ts, mode, trigger, source_id, size_bytes,
                 time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def _recordings_from_rows(self, rows: list) -> list[Recording]:
        return [Recording(*row) for row in rows]

    def recording_covering(self, ts: float, source_id: int | None = None
                           ) -> Recording | None:
        query = ("SELECT id, kind, path, start_ts, end_ts, mode, trigger, source_id,"
                 " size_bytes, created_at FROM recordings"
                 " WHERE start_ts <= ? AND (end_ts IS NULL OR ? <= end_ts)")
        params: list[object] = [ts, ts]
        if source_id is not None:
            query += " AND source_id = ?"
            params.append(source_id)
        query += " ORDER BY start_ts DESC LIMIT 1"
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
        return Recording(*row) if row else None

    def list_recordings(self, limit: int = 200, kind: str | None = None
                        ) -> list[Recording]:
        query = ("SELECT id, kind, path, start_ts, end_ts, mode, trigger, source_id,"
                 " size_bytes, created_at FROM recordings")
        params: list[object] = []
        if kind is not None:
            query += " WHERE kind = ?"
            params.append(kind)
        query += " ORDER BY start_ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return self._recordings_from_rows(rows)

    def list_recordings_older_than(self, ts: float) -> list[Recording]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, kind, path, start_ts, end_ts, mode, trigger, source_id,"
                " size_bytes, created_at FROM recordings WHERE start_ts < ?"
                " ORDER BY start_ts ASC", (ts,)
            ).fetchall()
        return self._recordings_from_rows(rows)

    def delete_recording(self, recording_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
            self._conn.commit()

    def total_recordings_size(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM recordings"
            ).fetchone()
        return int(row[0])

    def oldest_recording_ts(self) -> float | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT MIN(start_ts) FROM recordings"
            ).fetchone()
        return float(row[0]) if row[0] is not None else None

    def count_recordings(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM recordings").fetchone()
        return int(row[0])

    # -- statistics ----------------------------------------------------------
    def upsert_daily_stat(self, day_start: float, source_id: int,
                          event_type: str, count: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO statistics (day_start, source_id, event_type, count,"
                " updated_at) VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(day_start, source_id, event_type)"
                " DO UPDATE SET count = excluded.count, updated_at = excluded.updated_at",
                (day_start, source_id, event_type, count, time.time()),
            )
            self._conn.commit()

    def daily_stats(self, start_day: float, end_day: float,
                    source_id: int | None = None) -> list[DailyStat]:
        query = ("SELECT day_start, source_id, event_type, count, updated_at"
                 " FROM statistics WHERE day_start >= ? AND day_start < ?")
        params: list[object] = [start_day, end_day]
        if source_id is not None:
            query += " AND source_id = ?"
            params.append(source_id)
        query += " ORDER BY day_start ASC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [DailyStat(*row) for row in rows]

    def search_events(self, start: float, end: float,
                      types: list[str] | None = None, source_id: int | None = None,
                      limit: int = 1000) -> list[StoredEvent]:
        query = ("SELECT id, ts, type, source_id, label, confidence, bbox,"
                 " snapshot_path, metadata FROM events WHERE ts >= ? AND ts < ?")
        params: list[object] = [start, end]
        if types:
            placeholders = ",".join("?" for _ in types)
            query += f" AND type IN ({placeholders})"
            params.extend(types)
        if source_id is not None:
            query += " AND source_id = ?"
            params.append(source_id)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        result: list[StoredEvent] = []
        for row in rows:
            bbox = tuple(json.loads(row[6])) if row[6] else None
            result.append(StoredEvent(
                id=row[0], timestamp=row[1], type=row[2], source_id=row[3],
                label=row[4], confidence=row[5], bbox=bbox, snapshot_path=row[7],
                metadata=json.loads(row[8]),
            ))
        return result

    def event_type_counts(self, start: float, end: float,
                          source_id: int | None = None) -> dict[str, int]:
        query = "SELECT type, COUNT(*) FROM events WHERE ts >= ? AND ts < ?"
        params: list[object] = [start, end]
        if source_id is not None:
            query += " AND source_id = ?"
            params.append(source_id)
        query += " GROUP BY type"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return {row[0]: int(row[1]) for row in rows}

    def daily_rollup_counts(self, day_start: float,
                            day_end: float) -> list[tuple[int, str, int]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT COALESCE(source_id, 0), type, COUNT(*) FROM events"
                " WHERE ts >= ? AND ts < ? GROUP BY COALESCE(source_id, 0), type",
                (day_start, day_end),
            ).fetchall()
        return [(int(r[0]), r[1], int(r[2])) for r in rows]

    # -- tracklets -------------------------------------------------------------
    def add_tracklet(self, source_id: int | None, track_id: int | None,
                     first_ts: float, now: float) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tracklets (source_id, track_id, first_ts, last_ts,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (source_id, track_id, first_ts, first_ts, now, now),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def update_tracklet_attributes(
        self, tracklet_id: int, *, height_band: str | None, build: str | None,
        upper_color: str | None, lower_color: str | None, clothing_type: str | None,
        accessories: list[str], attr_conf: float | None, snapshot_path: str | None,
        last_ts: float, now: float,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tracklets SET height_band=?, build=?, upper_color=?,"
                " lower_color=?, clothing_type=?, accessories=?, attr_conf=?,"
                " snapshot_path=COALESCE(?, snapshot_path), last_ts=?,"
                " obs_count=obs_count+1, updated_at=? WHERE id=?",
                (height_band, build, upper_color, lower_color, clothing_type,
                 json.dumps(accessories), attr_conf, snapshot_path, last_ts, now,
                 tracklet_id),
            )
            self._conn.commit()

    def set_tracklet_embedding(self, tracklet_id: int, vector: bytes, dim: int,
                               model_id: str, now: float) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tracklet_embeddings (tracklet_id, dim, model_id, vector,"
                " created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(tracklet_id)"
                " DO UPDATE SET dim=excluded.dim, model_id=excluded.model_id,"
                " vector=excluded.vector, created_at=excluded.created_at",
                (tracklet_id, dim, model_id, vector, now),
            )
            self._conn.commit()

    def _tracklet_from_row(self, row: tuple) -> "Tracklet":
        return Tracklet(
            id=row[0], source_id=row[1], track_id=row[2], first_ts=row[3],
            last_ts=row[4], obs_count=row[5], snapshot_path=row[6], height_band=row[7],
            build=row[8], upper_color=row[9], lower_color=row[10], clothing_type=row[11],
            accessories=json.loads(row[12]), attr_conf=row[13], pinned=row[14],
            created_at=row[15], updated_at=row[16],
        )

    def get_tracklet(self, tracklet_id: int) -> "Tracklet | None":
        with self._lock:
            row = self._conn.execute(
                "SELECT id, source_id, track_id, first_ts, last_ts, obs_count,"
                " snapshot_path, height_band, build, upper_color, lower_color,"
                " clothing_type, accessories, attr_conf, pinned, created_at, updated_at"
                " FROM tracklets WHERE id=?", (tracklet_id,)
            ).fetchone()
        return self._tracklet_from_row(row) if row else None

    _CROSS_ATTRS = ("upper_color", "lower_color", "build", "height_band", "clothing_type")

    def cross_camera_candidates(self, tracklet_id: int, *, window_seconds: float = 300.0,
                                min_score: int = 2, limit: int = 10,
                                ) -> list[tuple["Tracklet", int]]:
        """Anonymous tracklets on OTHER cameras, near in time, whose appearance
        attributes match the given tracklet — a suggested cross-camera movement
        path. OSINT-safe: no biometric match, only anonymized attribute
        similarity; scored by matching-attribute count then temporal closeness."""
        src = self.get_tracklet(tracklet_id)
        if src is None or src.source_id is None:
            return []
        lo, hi = src.last_ts - window_seconds, src.last_ts + window_seconds
        query = (
            "SELECT id, source_id, track_id, first_ts, last_ts, obs_count,"
            " snapshot_path, height_band, build, upper_color, lower_color,"
            " clothing_type, accessories, attr_conf, pinned, created_at, updated_at"
            " FROM tracklets WHERE source_id IS NOT NULL AND source_id != ?"
            " AND id != ? AND last_ts BETWEEN ? AND ?")
        with self._lock:
            rows = self._conn.execute(
                query, (src.source_id, tracklet_id, lo, hi)).fetchall()
        scored: list[tuple[Tracklet, int]] = []
        for row in rows:
            cand = self._tracklet_from_row(row)
            score = sum(1 for attr in self._CROSS_ATTRS
                        if getattr(src, attr) is not None
                        and getattr(src, attr) == getattr(cand, attr))
            if score >= min_score:
                scored.append((cand, score))
        scored.sort(key=lambda cs: (-cs[1], abs(cs[0].last_ts - src.last_ts)))
        return scored[:limit]

    def cross_camera_flows(self, *, window_seconds: float = 900.0, min_score: int = 2,
                           max_tracklets: int = 300) -> dict[tuple[int, int], int]:
        """Aggregate Phase 23 cross-camera candidate links into directed
        (src_source_id, dst_source_id) -> count flow counts, for drawing
        suggested movement flows between camera nodes on the topology map.
        Bounded to the most recent `max_tracklets` tracklets to stay cheap."""
        flows: dict[tuple[int, int], int] = {}
        recent = self.search_tracklets(limit=max_tracklets)
        for t in recent:
            if t.source_id is None:
                continue
            for cand, _score in self.cross_camera_candidates(
                t.id, window_seconds=window_seconds, min_score=min_score,
            ):
                if cand.source_id is None:
                    continue
                key = (t.source_id, cand.source_id)
                flows[key] = flows.get(key, 0) + 1
        return flows

    def get_embedding(self, tracklet_id: int) -> tuple[bytes, int] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT vector, dim FROM tracklet_embeddings WHERE tracklet_id=?",
                (tracklet_id,),
            ).fetchone()
        return (row[0], int(row[1])) if row else None

    def candidate_embeddings(
        self, exclude_id: int, *, source_id: int | None = None,
        upper_color: str | None = None, lower_color: str | None = None,
        clothing_type: str | None = None, since: float | None = None,
        until: float | None = None,
    ) -> list[tuple[int, bytes, int]]:
        query = ("SELECT t.id, e.vector, e.dim FROM tracklets t"
                 " JOIN tracklet_embeddings e ON e.tracklet_id=t.id WHERE t.id != ?")
        params: list[object] = [exclude_id]
        for column, value in (("source_id", source_id), ("upper_color", upper_color),
                              ("lower_color", lower_color),
                              ("clothing_type", clothing_type)):
            if value is not None:
                query += f" AND t.{column} = ?"
                params.append(value)
        if since is not None:
            query += " AND t.last_ts >= ?"
            params.append(since)
        if until is not None:
            query += " AND t.last_ts < ?"
            params.append(until)
        query += " ORDER BY t.id"      # deterministic tie order for top-K NN
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [(int(r[0]), r[1], int(r[2])) for r in rows]

    def search_tracklets(
        self, *, colors: list[str] | None = None,
        clothing_types: list[str] | None = None, accessories: list[str] | None = None,
        height_bands: list[str] | None = None, builds: list[str] | None = None,
        source_id: int | None = None, start: float | None = None,
        end: float | None = None, limit: int = 500,
    ) -> list["Tracklet"]:
        query = (
            "SELECT id, source_id, track_id, first_ts, last_ts, obs_count,"
            " snapshot_path, height_band, build, upper_color, lower_color,"
            " clothing_type, accessories, attr_conf, pinned, created_at, updated_at"
            " FROM tracklets WHERE 1=1")
        params: list[object] = []
        if colors:
            ph = ",".join("?" for _ in colors)
            query += f" AND (upper_color IN ({ph}) OR lower_color IN ({ph}))"
            params.extend(colors)
            params.extend(colors)
        if clothing_types:
            ph = ",".join("?" for _ in clothing_types)
            query += f" AND clothing_type IN ({ph})"
            params.extend(clothing_types)
        if height_bands:
            ph = ",".join("?" for _ in height_bands)
            query += f" AND height_band IN ({ph})"
            params.extend(height_bands)
        if builds:
            ph = ",".join("?" for _ in builds)
            query += f" AND build IN ({ph})"
            params.extend(builds)
        for acc in accessories or []:
            query += " AND accessories LIKE ?"
            params.append(f"%{json.dumps(acc)}%")
        # When filtering on appearance, drop tracklets whose attributes were read with low confidence
        # (blurry/far crops) — those are the source of "irrelevant" hits for a colour/type search.
        if colors or clothing_types or height_bands or builds:
            query += " AND (attr_conf IS NULL OR attr_conf >= 0.30)"
        if source_id is not None:
            query += " AND source_id = ?"
            params.append(source_id)
        if start is not None:
            query += " AND last_ts >= ?"
            params.append(start)
        if end is not None:
            query += " AND last_ts < ?"
            params.append(end)
        query += " ORDER BY last_ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._tracklet_from_row(row) for row in rows]

    def set_tracklet_pinned(self, tracklet_id: int, pinned: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE tracklets SET pinned=? WHERE id=?",
                               (pinned, tracklet_id))
            self._conn.commit()

    def count_tracklets(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM tracklets").fetchone()
        return int(row[0])

    def prune_tracklets_older_than(self, ts: float) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM tracklets WHERE created_at < ? AND pinned = 0", (ts,)
            )
            self._conn.commit()
            return int(cur.rowcount)

    def tracklet_snapshots_to_prune(self, ts: float) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT snapshot_path FROM tracklets WHERE created_at < ?"
                " AND pinned = 0 AND snapshot_path IS NOT NULL", (ts,)
            ).fetchall()
        return [row[0] for row in rows]

    def pinned_tracklet_snapshots(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT snapshot_path FROM tracklets WHERE pinned = 1"
                " AND snapshot_path IS NOT NULL"
            ).fetchall()
        return [row[0] for row in rows]

    def unpinned_tracklet_snapshots(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT snapshot_path FROM tracklets WHERE pinned = 0"
                " AND snapshot_path IS NOT NULL"
            ).fetchall()
        return [row[0] for row in rows]

    def purge_unpinned_tracklets(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM tracklets WHERE pinned = 0")
            self._conn.commit()
            return int(cur.rowcount)

    # -- subjects (long-term cross-session identity, features 5/6) ------------
    _SUBJ_COLS = ("id, cls, label, first_seen, last_seen, sighting_count, day_count, last_day,"
                  " plate, attrs, snapshot_path, watched, flags, created_at, updated_at")

    @staticmethod
    def _subject_row(row: tuple) -> dict:
        return {
            "id": row[0], "cls": row[1], "label": row[2], "first_seen": row[3], "last_seen": row[4],
            "sighting_count": row[5], "day_count": row[6], "last_day": row[7], "plate": row[8],
            "attrs": json.loads(row[9] or "{}"), "snapshot_path": row[10], "watched": bool(row[11]),
            "flags": json.loads(row[12] or "[]"), "created_at": row[13], "updated_at": row[14],
        }

    def add_subject(self, cls: str, now: float, *, day: str, plate: str | None = None,
                    attrs: dict | None = None, snapshot_path: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO subjects (cls, first_seen, last_seen, sighting_count, day_count,"
                " last_day, plate, attrs, snapshot_path, created_at, updated_at)"
                " VALUES (?, ?, ?, 1, 1, ?, ?, ?, ?, ?, ?)",
                (cls, now, now, day, plate, json.dumps(attrs or {}), snapshot_path, now, now),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def touch_subject(self, subject_id: int, now: float, *, day: str,
                      snapshot_path: str | None = None, plate: str | None = None,
                      attrs: dict | None = None) -> None:
        """Record another sighting of an existing subject: bump last_seen + counts, roll the distinct
        day counter when the day changes, and adopt a better snapshot/plate when offered."""
        with self._lock:
            row = self._conn.execute(
                "SELECT last_day, snapshot_path FROM subjects WHERE id=?", (subject_id,)).fetchone()
            if row is None:
                return
            new_day = 1 if row[0] != day else 0
            sets = ["last_seen=?", "sighting_count=sighting_count+1",
                    "day_count=day_count+?", "last_day=?", "updated_at=?"]
            args: list = [now, new_day, day, now]
            if snapshot_path and not row[1]:
                sets.append("snapshot_path=?"); args.append(snapshot_path)
            if plate:
                sets.append("plate=?"); args.append(plate)
            if attrs is not None:
                sets.append("attrs=?"); args.append(json.dumps(attrs))
            args.append(subject_id)
            self._conn.execute(f"UPDATE subjects SET {', '.join(sets)} WHERE id=?", args)
            self._conn.commit()

    def add_sighting(self, subject_id: int, ts: float, *, source_id: int | None = None,
                     cam: str | None = None, snapshot_path: str | None = None,
                     clip_path: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sightings (subject_id, source_id, cam, ts, snapshot_path, clip_path)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (subject_id, source_id, cam, ts, snapshot_path, clip_path),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def add_subject_descriptor(self, subject_id: int, kind: str, vector: bytes, dim: int,
                               model_id: str, ts: float, *, quality: float = 1.0, cap: int = 12) -> None:
        """Append a descriptor to a subject's gallery, keeping at most `cap` most-recent per kind so
        matching averages over many crops without the table growing without bound."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO subject_descriptors (subject_id, kind, dim, model_id, vector, quality, ts)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (subject_id, kind, dim, model_id, vector, quality, ts),
            )
            self._conn.execute(
                "DELETE FROM subject_descriptors WHERE id IN (SELECT id FROM subject_descriptors"
                " WHERE subject_id=? AND kind=? ORDER BY ts DESC LIMIT -1 OFFSET ?)",
                (subject_id, kind, cap),
            )
            self._conn.commit()

    def subject_descriptors(self, cls: str, kind: str, *, since: float | None = None,
                            limit: int = 20000) -> list[tuple[int, bytes, int]]:
        """(subject_id, vector, dim) rows of one kind for candidate matching, newest first, optionally
        only for subjects seen since `since` (a warm recent-subjects window)."""
        q = ("SELECT d.subject_id, d.vector, d.dim FROM subject_descriptors d"
             " JOIN subjects s ON s.id = d.subject_id WHERE s.cls=? AND d.kind=?")
        args: list = [cls, kind]
        if since is not None:
            q += " AND s.last_seen >= ?"; args.append(since)
        q += " ORDER BY d.ts DESC LIMIT ?"; args.append(limit)
        with self._lock:
            return [(r[0], r[1], r[2]) for r in self._conn.execute(q, args).fetchall()]

    def get_subject(self, subject_id: int) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {self._SUBJ_COLS} FROM subjects WHERE id=?", (subject_id,)).fetchone()
        return self._subject_row(row) if row else None

    def list_subjects(self, *, cls: str | None = None, limit: int = 200,
                      order: str = "last_seen") -> list[dict]:
        col = {"last_seen": "last_seen", "first_seen": "first_seen",
               "sighting_count": "sighting_count", "day_count": "day_count"}.get(order, "last_seen")
        q = f"SELECT {self._SUBJ_COLS} FROM subjects"
        args: list = []
        if cls:
            q += " WHERE cls=?"; args.append(cls)
        q += f" ORDER BY {col} DESC LIMIT ?"; args.append(limit)
        with self._lock:
            return [self._subject_row(r) for r in self._conn.execute(q, args).fetchall()]

    def list_sightings(self, subject_id: int, *, limit: int = 500) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, source_id, cam, ts, snapshot_path, clip_path FROM sightings"
                " WHERE subject_id=? ORDER BY ts DESC LIMIT ?", (subject_id, limit)).fetchall()
        return [{"id": r[0], "source_id": r[1], "cam": r[2], "ts": r[3],
                 "snapshot_path": r[4], "clip_path": r[5]} for r in rows]

    def subject_dossier(self, subject_id: int) -> dict | None:
        """Aggregate a subject's whole history: per-camera counts, hour-of-day histogram, and the
        number of distinct calendar days seen. Powers the repeat-visitor dossier view."""
        subj = self.get_subject(subject_id)
        if subj is None:
            return None
        with self._lock:
            per_cam = self._conn.execute(
                "SELECT cam, COUNT(*) FROM sightings WHERE subject_id=? GROUP BY cam"
                " ORDER BY COUNT(*) DESC", (subject_id,)).fetchall()
            hours = self._conn.execute(
                "SELECT CAST((ts % 86400) / 3600 AS INT) h, COUNT(*) FROM sightings"
                " WHERE subject_id=? GROUP BY h", (subject_id,)).fetchall()
            days = self._conn.execute(
                "SELECT COUNT(DISTINCT date(ts,'unixepoch')) FROM sightings WHERE subject_id=?",
                (subject_id,)).fetchone()
        hist = [0] * 24
        for h, c in hours:
            if h is not None and 0 <= int(h) < 24:
                hist[int(h)] = int(c)
        subj["per_camera"] = [{"cam": c or "?", "count": int(n)} for c, n in per_cam]
        subj["hour_histogram"] = hist
        subj["distinct_days"] = int(days[0]) if days else 0
        return subj

    def set_subject_flags(self, subject_id: int, flags: list[str], now: float) -> None:
        with self._lock:
            self._conn.execute("UPDATE subjects SET flags=?, updated_at=? WHERE id=?",
                               (json.dumps(sorted(set(flags))), now, subject_id))
            self._conn.commit()

    def set_subject_watched(self, subject_id: int, watched: bool, now: float) -> None:
        with self._lock:
            self._conn.execute("UPDATE subjects SET watched=?, updated_at=? WHERE id=?",
                               (1 if watched else 0, now, subject_id))
            self._conn.commit()

    def set_subject_label(self, subject_id: int, label: str | None, now: float) -> None:
        with self._lock:
            self._conn.execute("UPDATE subjects SET label=?, updated_at=? WHERE id=?",
                               (label, now, subject_id))
            self._conn.commit()

    def merge_subjects(self, keep_id: int, drop_id: int, now: float) -> None:
        """Fold `drop` into `keep`: re-point its sightings + descriptors, sum counts, widen the seen
        window. Used when two persisted subjects turn out to be the same person."""
        if keep_id == drop_id:
            return
        with self._lock:
            k = self._conn.execute("SELECT first_seen, last_seen, sighting_count, day_count"
                                   " FROM subjects WHERE id=?", (keep_id,)).fetchone()
            d = self._conn.execute("SELECT first_seen, last_seen, sighting_count, day_count"
                                   " FROM subjects WHERE id=?", (drop_id,)).fetchone()
            if k is None or d is None:
                return
            self._conn.execute("UPDATE sightings SET subject_id=? WHERE subject_id=?", (keep_id, drop_id))
            self._conn.execute("UPDATE subject_descriptors SET subject_id=? WHERE subject_id=?",
                               (keep_id, drop_id))
            self._conn.execute(
                "UPDATE subjects SET first_seen=?, last_seen=?, sighting_count=?, day_count=?,"
                " updated_at=? WHERE id=?",
                (min(k[0], d[0]), max(k[1], d[1]), k[2] + d[2], k[3] + d[3], now, keep_id))
            self._conn.execute("DELETE FROM subjects WHERE id=?", (drop_id,))
            self._conn.commit()

    def prune_subjects_older_than(self, ts: float) -> list[str]:
        """Delete subjects (never watched) whose last sighting predates `ts`; return their snapshot
        paths so the caller can remove the files. Cascades descriptors + sightings."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, snapshot_path FROM subjects WHERE watched=0 AND last_seen < ?",
                (ts,)).fetchall()
            ids = [r[0] for r in rows]
            snaps = [r[1] for r in rows if r[1]]
            for sid in ids:
                self._conn.execute("DELETE FROM subjects WHERE id=?", (sid,))
            self._conn.commit()
        return snaps

    # -- zones ---------------------------------------------------------------
    def add_zone(self, source_id: int | None, name: str, type: str,
                 polygon: list[tuple[int, int]], loiter_seconds: float | None,
                 allowed_direction: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO zones (source_id, name, type, polygon, loiter_seconds,"
                " allowed_direction, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source_id, name, type, json.dumps([list(p) for p in polygon]),
                 loiter_seconds, allowed_direction, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_zones(self, source_id: int | None = None) -> list["Zone"]:
        query = ("SELECT id, source_id, name, type, polygon, loiter_seconds,"
                 " allowed_direction, created_at FROM zones")
        params: list[object] = []
        if source_id is not None:
            query += " WHERE source_id = ?"
            params.append(source_id)
        query += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [Zone(id=r[0], source_id=r[1], name=r[2], type=r[3],
                     polygon=[tuple(p) for p in json.loads(r[4])],
                     loiter_seconds=r[5], allowed_direction=r[6], created_at=r[7])
                for r in rows]

    def delete_zone(self, zone_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM zones WHERE id = ?", (zone_id,))
            self._conn.commit()

    def update_zone(self, zone_id: int, name: str, type: str,
                    loiter_seconds: float | None,
                    allowed_direction: str | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE zones SET name=?, type=?, loiter_seconds=?,"
                " allowed_direction=? WHERE id=?",
                (name, type, loiter_seconds, allowed_direction, zone_id))
            self._conn.commit()

    # -- alert rules ---------------------------------------------------------
    def add_alert_rule(self, name: str, event_type: str, *,
                       source_id: int | None = None, zone_id: int | None = None,
                       min_count: int | None = None, min_duration_s: float | None = None,
                       min_confidence: float | None = None, severity: str = "warning",
                       cooldown_s: float = 60.0, enabled: bool = True) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO alert_rules (name, event_type, source_id, zone_id,"
                " min_count, min_duration_s, min_confidence, severity, cooldown_s,"
                " enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, event_type, source_id, zone_id, min_count, min_duration_s,
                 min_confidence, severity, cooldown_s, int(enabled), time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_alert_rules(self) -> list[AlertRule]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, event_type, source_id, zone_id, min_count,"
                " min_duration_s, min_confidence, severity, cooldown_s, enabled"
                " FROM alert_rules ORDER BY id"
            ).fetchall()
        return [AlertRule(id=r[0], name=r[1], event_type=r[2], source_id=r[3],
                          zone_id=r[4], min_count=r[5], min_duration_s=r[6],
                          min_confidence=r[7], severity=r[8], cooldown_s=r[9],
                          enabled=bool(r[10])) for r in rows]

    def update_alert_rule(self, rule_id: int, name: str, event_type: str,
                          source_id: int | None, zone_id: int | None,
                          min_count: int | None, min_duration_s: float | None,
                          min_confidence: float | None, severity: str,
                          cooldown_s: float, enabled: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE alert_rules SET name=?, event_type=?, source_id=?, zone_id=?,"
                " min_count=?, min_duration_s=?, min_confidence=?, severity=?,"
                " cooldown_s=?, enabled=? WHERE id=?",
                (name, event_type, source_id, zone_id, min_count, min_duration_s,
                 min_confidence, severity, cooldown_s, int(enabled), rule_id),
            )
            self._conn.commit()

    def delete_alert_rule(self, rule_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
            self._conn.commit()

    def seed_default_alert_rules(self, crowd_min: int, cooldown_s: float) -> None:
        if self.get_setting("alert_rules_seeded") is not None:
            return
        self.add_alert_rule("Abandoned Object", "ABANDONED_OBJECT",
                            severity="critical", cooldown_s=cooldown_s)
        self.add_alert_rule("Restricted Zone Breach", "RESTRICTED",
                            severity="critical", cooldown_s=cooldown_s)
        self.add_alert_rule("Crowding", "CROWDING", min_count=crowd_min,
                            severity="warning", cooldown_s=cooldown_s)
        self.add_alert_rule("Loitering", "LOITERING", severity="warning",
                            cooldown_s=cooldown_s)
        self.set_setting("alert_rules_seeded", "1")

    def seed_default_source(self) -> None:
        """On a brand-new database, add the public demo cameras so a fresh install
        shows cameras spread over the map and can connect right away. Runs once.

        A second pass tops up installs seeded back when there was only one demo
        camera, but only while every source still present is a demo one, so an
        operator's own camera list is never touched.
        """
        first_run = self.get_setting("default_source_seeded") is None
        self.set_setting("default_source_seeded", "1")
        with self._lock:
            rows = self._conn.execute("SELECT url FROM sources").fetchall()
        have = {r[0] for r in rows}
        if first_run:
            if have:
                return  # the operator already has cameras; leave them alone
        else:
            if self.get_setting("demo_sources_v2_seeded") is not None:
                return
            if not have.issubset({c[1] for c in DEMO_SOURCES}):
                # cameras the operator added themselves; don't top up around them
                self.set_setting("demo_sources_v2_seeded", "1")
                return
        for name, url, lat, lng in DEMO_SOURCES:
            if url in have:
                continue
            self.update_source(self.add_source(name, url), name, url, lat, lng)
        self.set_setting("demo_sources_v2_seeded", "1")

    # -- alerts --------------------------------------------------------------
    def add_alert(self, alert: Alert) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO alerts (ts, rule_id, rule_name, event_type, source_id,"
                " severity, summary, snapshot_path, clip_path, acknowledged, metadata)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (alert.timestamp, alert.rule_id, alert.rule_name, alert.event_type,
                 alert.source_id, alert.severity, alert.summary, alert.snapshot_path,
                 alert.clip_path, int(alert.acknowledged), json.dumps(alert.metadata)),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_alerts(self, limit: int = 200, since: float | None = None,
                    unacked_only: bool = False) -> list[Alert]:
        query = ("SELECT id, ts, rule_id, rule_name, event_type, source_id, severity,"
                 " summary, snapshot_path, acknowledged, metadata, clip_path FROM alerts")
        clauses: list[str] = []
        params: list[object] = []
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        if unacked_only:
            clauses.append("acknowledged = 0")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [Alert(id=r[0], timestamp=r[1], rule_id=r[2], rule_name=r[3],
                      event_type=r[4], source_id=r[5], severity=r[6], summary=r[7],
                      snapshot_path=r[8], acknowledged=bool(r[9]),
                      metadata=json.loads(r[10]), clip_path=r[11]) for r in rows]

    def acknowledge_alert(self, alert_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE alerts SET acknowledged = 1 WHERE id = ?", (alert_id,))
            self._conn.commit()

    def delete_alerts_older_than(self, ts: float) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM alerts WHERE ts < ?", (ts,))
            self._conn.commit()
            return int(cur.rowcount)

    # -- cases -----------------------------------------------------------------
    def add_case(self, name: str, *, threat_level: str = "low", notes: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO cases (name, threat_level, notes, created_at)"
                " VALUES (?, ?, ?, ?)",
                (name, threat_level, notes, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_cases(self) -> list[Case]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, threat_level, notes, created_at, status FROM cases ORDER BY id DESC"
            ).fetchall()
        return [Case(*row) for row in rows]

    def get_case(self, case_id: int) -> Case | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, threat_level, notes, created_at, status FROM cases WHERE id=?",
                (case_id,),
            ).fetchone()
        return Case(*row) if row else None

    def set_case_status(self, case_id: int, status: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE cases SET status=? WHERE id=?", (status, case_id))
            self._conn.commit()

    def add_case_event(self, case_id: int, ts: float, kind: str, *, event_type: str = "",
                       cam: str = "", severity: str = "info", summary: str = "",
                       snapshot: str | None = None, clip: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO case_events (case_id, ts, kind, event_type, cam, severity, summary,"
                " snapshot, clip, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, ts, kind, event_type, cam, severity, summary, snapshot, clip, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_case_events(self, case_id: int) -> list[CaseEvent]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, case_id, ts, kind, event_type, cam, severity, summary, snapshot,"
                " clip, created_at FROM case_events WHERE case_id=? ORDER BY ts", (case_id,),
            ).fetchall()
        return [CaseEvent(*row) for row in rows]

    def update_case(self, case_id: int, name: str, threat_level: str, notes: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE cases SET name=?, threat_level=?, notes=? WHERE id=?",
                (name, threat_level, notes, case_id),
            )
            self._conn.commit()

    def add_case_target(self, case_id: int, tracklet_id: int, alias: str = "") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO case_targets (case_id, tracklet_id, alias, added_at)"
                " VALUES (?, ?, ?, ?)",
                (case_id, tracklet_id, alias, time.time()),
            )
            self._conn.commit()
            target_id = int(cur.lastrowid)
        self.set_tracklet_pinned(tracklet_id, 1)  # assigning to a case pins the tracklet
        return target_id

    def list_case_targets(self, case_id: int) -> list[CaseTarget]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, case_id, tracklet_id, alias, added_at FROM case_targets"
                " WHERE case_id=? ORDER BY id", (case_id,)
            ).fetchall()
        return [CaseTarget(*row) for row in rows]

    def remove_case_target(self, target_id: int) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT tracklet_id FROM case_targets WHERE id=?", (target_id,)
            ).fetchone()
            if row is None:
                return
            tracklet_id = int(row[0])
            self._conn.execute("DELETE FROM case_targets WHERE id=?", (target_id,))
            self._conn.commit()
            remaining = self._conn.execute(
                "SELECT COUNT(*) FROM case_targets WHERE tracklet_id=?", (tracklet_id,)
            ).fetchone()[0]
        if remaining == 0:
            self.set_tracklet_pinned(tracklet_id, 0)

    def delete_case(self, case_id: int) -> None:
        with self._lock:
            rows = self._conn.execute(
                "SELECT tracklet_id FROM case_targets WHERE case_id=?", (case_id,)
            ).fetchall()
            tracklet_ids = [int(r[0]) for r in rows]
            self._conn.execute("DELETE FROM case_targets WHERE case_id=?", (case_id,))
            self._conn.execute("DELETE FROM cases WHERE id=?", (case_id,))
            self._conn.commit()
            remaining_by_tid = {}
            for tid in set(tracklet_ids):
                remaining_by_tid[tid] = self._conn.execute(
                    "SELECT COUNT(*) FROM case_targets WHERE tracklet_id=?", (tid,)
                ).fetchone()[0]
        for tid, remaining in remaining_by_tid.items():
            if remaining == 0:
                self.set_tracklet_pinned(tid, 0)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
