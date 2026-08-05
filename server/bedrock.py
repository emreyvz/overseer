"""BEDROCK — a bitemporal fact store projected from the tables that already exist.

Video systems store detections in flat rows because that is what search needs. It is also why
the only questions you can ask are the ones somebody indexed for. BEDROCK adds a second reading
of the same data: typed assertions, each carrying its provenance and two independent time axes.

  VALID TIME       when the thing was true in the world
  TRANSACTION TIME when we came to believe it

A correction never deletes: it closes `tx_to` on the old row and inserts a new one. That single
discipline is what makes "what did we believe last Tuesday" answerable, and it is the difference
between an analysis and an alibi.

BEDROCK is a PROJECTION, never the source of truth. Detections, sightings, events, alerts and
subjects stay exactly where they are; a projector reads them and emits facts. If the projection
is ever wrong it can be dropped and rebuilt.

Two things decide whether this ships at all:

**Interval compression.** Writing a fact per observation at 3 fps with five subjects is >150k
rows an hour and the disk is gone by Friday. Every predicate goes through a debouncer: a value
must hold for K observations before an interval opens, and be absent for K more before it
closes. A person crossing a scene produces roughly a dozen facts, not twelve hundred. There is a
hard per-hour write budget on top, and exceeding it raises the thresholds rather than the disk
usage.

**A closed vocabulary and a typed query AST.** The LLM emits an AST, never SQL: it is
validatable, retryable, injection-free, and it renders back into the operator's screen as chips
so they can see exactly what was asked before it runs.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

log = logging.getLogger("overseer.bedrock")

VOCAB_VERSION = 1

# The closed predicate vocabulary. Open vocabularies make the query UI impossible to build and
# the store impossible to reason about; every entry below maps to data the platform already
# produces.
PREDICATES: dict[str, dict] = {
    # presence
    "seen_on": {"family": "presence", "object": "entity", "label": "WAS SEEN ON"},
    "present_in": {"family": "presence", "object": "entity", "label": "WAS INSIDE"},
    "entered": {"family": "presence", "object": "entity", "label": "ENTERED"},
    "exited": {"family": "presence", "object": "entity", "label": "LEFT"},
    # spatial / social
    "near": {"family": "spatial", "object": "entity", "label": "WAS NEAR"},
    "co_present_with": {"family": "spatial", "object": "entity", "label": "WAS THERE WITH"},
    "occluded_by": {"family": "spatial", "object": "entity", "label": "WAS HIDDEN BY"},
    # appearance (observed, never used for behavioural scoring — see server/grain.py)
    "wore": {"family": "appearance", "object": "literal", "label": "WORE"},
    "has_plate": {"family": "appearance", "object": "literal", "label": "HAS PLATE"},
    "is_subtype": {"family": "appearance", "object": "literal", "label": "IS A"},
    "is_bodytype": {"family": "appearance", "object": "literal", "label": "HAS BODY TYPE"},
    "has_make": {"family": "appearance", "object": "literal", "label": "IS MADE BY"},
    "estimated_height": {"family": "appearance", "object": "number", "label": "IS ROUGHLY (CM)"},
    # behaviour
    "intent": {"family": "behaviour", "object": "literal", "label": "APPEARED TO BE"},
    "moving_at": {"family": "behaviour", "object": "number", "label": "WAS MOVING AT (KM/H)"},
    "dwelled": {"family": "behaviour", "object": "number", "label": "STOOD STILL FOR (S)"},
    "conformity": {"family": "behaviour", "object": "number", "label": "CONFORMITY PERCENTILE"},
    # identity
    "same_as": {"family": "identity", "object": "entity", "label": "IS THE SAME AS"},
    "flagged": {"family": "identity", "object": "literal", "label": "WAS FLAGGED"},
    "watched": {"family": "identity", "object": "literal", "label": "IS ON THE WATCHLIST"},
    # system: the perception suite writes here too, which is what makes it all searchable
    "alerted": {"family": "system", "object": "literal", "label": "RAISED THE ALERT"},
    "diverged": {"family": "system", "object": "number", "label": "DIVERGED BY (SIGMA)"},
    "vibrated": {"family": "system", "object": "number", "label": "VIBRATED AT (HZ)"},
    "unseen_at": {"family": "system", "object": "entity", "label": "COULD NOT BE OBSERVED AT"},
}

KINDS = ("person", "vehicle", "animal", "object", "zone", "camera", "event", "alert",
         "subject", "probe", "blindspot")

ALLEN = ("before", "after", "during", "overlaps", "meets", "starts", "finishes")
_NUM_OPS = {">=": ">=", "<=": "<=", "==": "=", ">": ">", "<": "<"}


class BedrockError(Exception):
    """A query the compiler refuses to run, with a reason the UI can act on."""

    def __init__(self, message: str, clause: int | None = None, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.clause = clause
        self.hint = hint


# ── the store ───────────────────────────────────────────────────────────────────────────────

class FactStore:
    """Reads and writes assertions. All SQL lives here; the projector above it only decides
    WHAT to assert, never how it is stored."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self._uid_cache: dict[tuple[str, str], int] = {}
        self._lock = threading.RLock()

    # -- entities ----------------------------------------------------------------------------
    def entity(self, kind: str, ref: str, *, label: str | None = None,
               snapshot: str | None = None, ts: float | None = None) -> int:
        """Get or create the entity for a native id. Cached: this is called per observation."""
        key = (kind, str(ref))
        uid = self._uid_cache.get(key)
        now = float(ts if ts is not None else time.time())
        if uid is not None:
            return uid
        with self._lock:
            rows = self.db.query("SELECT uid FROM bd_entity WHERE kind = ? AND ref = ?",
                                 (kind, str(ref)))
            if rows:
                uid = int(rows[0][0])
                self.db.execute("UPDATE bd_entity SET last_seen = ? WHERE uid = ?", (now, uid))
            else:
                uid = int(self.db.execute(
                    "INSERT INTO bd_entity (kind, ref, label, snapshot_path, first_seen, last_seen)"
                    " VALUES (?,?,?,?,?,?)", (kind, str(ref), label, snapshot, now, now)))
            if len(self._uid_cache) > 4000:
                self._uid_cache.clear()
            self._uid_cache[key] = uid
            return uid

    def touch(self, uid: int, ts: float, *, label: str | None = None,
              snapshot: str | None = None) -> None:
        sets = ["last_seen = ?"]
        params: list[Any] = [float(ts)]
        if label:
            sets.append("label = ?"); params.append(label)
        if snapshot:
            sets.append("snapshot_path = ?"); params.append(snapshot)
        params.append(int(uid))
        self.db.execute(f"UPDATE bd_entity SET {', '.join(sets)} WHERE uid = ?", params)

    def get_entity(self, uid: int) -> dict | None:
        rows = self.db.query(
            "SELECT uid, kind, ref, label, snapshot_path, first_seen, last_seen"
            " FROM bd_entity WHERE uid = ?", (int(uid),))
        if not rows:
            return None
        r = rows[0]
        return {"uid": int(r[0]), "kind": r[1], "ref": r[2], "label": r[3],
                "snapshot": r[4], "first_seen": float(r[5]) * 1000.0,
                "last_seen": float(r[6]) * 1000.0}

    # -- facts -------------------------------------------------------------------------------
    def open_fact(self, subj: int, pred: str, *, obj: int | None = None, val: str | None = None,
                  num: float | None = None, valid_from: float, conf: float = 1.0,
                  src_kind: str = "detector", src_ref: str | None = None,
                  model_id: str | None = None, snapshot: str | None = None) -> int:
        now = time.time()
        return int(self.db.execute(
            "INSERT INTO bd_fact (subj, pred, obj, val, num, valid_from, valid_to, tx_from,"
            " tx_to, conf, src_kind, src_ref, model_id, snapshot_path)"
            " VALUES (?,?,?,?,?,?,NULL,?,NULL,?,?,?,?,?)",
            (int(subj), pred, obj, val, num, float(valid_from), now, float(conf), src_kind,
             src_ref, model_id, snapshot)))

    def close_fact(self, fact_id: int, valid_to: float) -> None:
        self.db.execute("UPDATE bd_fact SET valid_to = ? WHERE id = ? AND valid_to IS NULL",
                        (float(valid_to), int(fact_id)))

    def supersede(self, fact_id: int, replacement: int | None = None) -> None:
        """Retract a belief WITHOUT destroying it.

        This is the whole reason for transaction time: a roster merge rewrites history, and an
        operator must still be able to see what the system believed before it did.
        """
        self.db.execute(
            "UPDATE bd_fact SET tx_to = ?, superseded_by = ? WHERE id = ? AND tx_to IS NULL",
            (time.time(), replacement, int(fact_id)))

    def facts_for(self, uid: int, *, current_only: bool = False, limit: int = 500) -> list[dict]:
        q = ("SELECT id, subj, pred, obj, val, num, valid_from, valid_to, tx_from, tx_to, conf,"
             " src_kind, src_ref, model_id, snapshot_path, superseded_by FROM bd_fact"
             " WHERE subj = ?")
        if current_only:
            q += " AND tx_to IS NULL AND valid_to IS NULL"
        q += " ORDER BY valid_from DESC LIMIT ?"
        return [_fact_row(r) for r in self.db.query(q, (int(uid), int(limit)))]

    def provenance(self, fact_id: int) -> dict:
        rows = self.db.query(
            "SELECT id, subj, pred, obj, val, num, valid_from, valid_to, tx_from, tx_to, conf,"
            " src_kind, src_ref, model_id, snapshot_path, superseded_by FROM bd_fact WHERE id = ?",
            (int(fact_id),))
        if not rows:
            return {"fact": None, "lineage": []}
        fact = _fact_row(rows[0])
        # the chain of what this belief replaced, so the UI can strike through the old claims
        lineage: list[dict] = []
        cur = fact_id
        for _ in range(12):
            prev = self.db.query(
                "SELECT id, subj, pred, obj, val, num, valid_from, valid_to, tx_from, tx_to,"
                " conf, src_kind, src_ref, model_id, snapshot_path, superseded_by"
                " FROM bd_fact WHERE superseded_by = ?", (int(cur),))
            if not prev:
                break
            lineage.append(_fact_row(prev[0]))
            cur = int(prev[0][0])
        return {"fact": fact, "lineage": lineage, "snapshot": fact.get("snapshot")}

    def stats(self) -> dict:
        f = self.db.query("SELECT COUNT(*), MIN(valid_from) FROM bd_fact")
        e = self.db.query("SELECT COUNT(*) FROM bd_entity")
        return {"facts": int(f[0][0] or 0), "entities": int(e[0][0] or 0),
                "oldest": (float(f[0][1]) * 1000.0) if f and f[0][1] else None}

    def purge_subject(self, uid: int) -> dict:
        """Hard erasure for one individual: every fact, every entity row, everything.

        A fact store is a far more powerful profiling instrument than a video archive, so the
        ability to destroy one person's record completely is not optional in half the world.
        """
        snaps = [r[0] for r in self.db.query(
            "SELECT snapshot_path FROM bd_fact WHERE (subj = ? OR obj = ?)"
            " AND snapshot_path IS NOT NULL", (int(uid), int(uid)))]
        n = self.db.query("SELECT COUNT(*) FROM bd_fact WHERE subj = ? OR obj = ?",
                          (int(uid), int(uid)))[0][0]
        self.db.execute("DELETE FROM bd_fact WHERE subj = ? OR obj = ?", (int(uid), int(uid)))
        self.db.execute("DELETE FROM bd_entity WHERE uid = ?", (int(uid),))
        self._uid_cache = {k: v for k, v in self._uid_cache.items() if v != uid}
        return {"facts": int(n), "entities": 1, "snapshots": len(snaps)}


def _fact_row(r: tuple) -> dict:
    return {
        "id": int(r[0]), "subj": int(r[1]), "pred": r[2],
        "obj": int(r[3]) if r[3] is not None else None,
        "val": r[4] if r[4] is not None else (None if r[5] is None else str(r[5])),
        "valid_from": float(r[6]) * 1000.0,
        "valid_to": float(r[7]) * 1000.0 if r[7] is not None else None,
        "tx_from": float(r[8]) * 1000.0,
        "tx_to": float(r[9]) * 1000.0 if r[9] is not None else None,
        "conf": float(r[10]), "src_kind": r[11], "src_ref": r[12], "model_id": r[13],
        "snapshot": r[14], "superseded_by": int(r[15]) if r[15] is not None else None,
    }


# ── the query compiler ──────────────────────────────────────────────────────────────────────

class QueryCompiler:
    """Compiles a typed AST into SQL with recursive CTEs.

    The LLM never emits SQL. It emits this AST, which is schema-validated, rendered back to the
    operator as chips before it runs, and cannot express anything the store does not support.
    """

    MAX_ROWS = 200_000            # refuse rather than melt
    MAX_CLAUSES = 12

    def __init__(self, store: FactStore) -> None:
        self.store = store
        self.db = store.db

    # -- validation --------------------------------------------------------------------------
    def validate(self, q: dict) -> None:
        where = q.get("where") or []
        if not isinstance(where, list):
            raise BedrockError("`where` must be a list of clauses")
        if len(where) > self.MAX_CLAUSES:
            raise BedrockError(f"too many clauses ({len(where)}); {self.MAX_CLAUSES} is the limit")
        win = q.get("window")
        if not where and not win:
            raise BedrockError("QUERY IS UNBOUNDED", hint="window")
        for i, c in enumerate(where):
            self._validate_clause(c, i, where, win)

    def _validate_clause(self, c: dict, i: int, where: list, win: dict | None) -> None:
        t = c.get("t")
        if t == "kind":
            if c.get("kind") not in KINDS:
                raise BedrockError(f"unknown entity kind: {c.get('kind')}", clause=i)
        elif t == "pred":
            if c.get("pred") not in PREDICATES:
                raise BedrockError(f"unknown predicate: {c.get('pred')}", clause=i)
            if c.get("op") and c["op"] not in _NUM_OPS:
                raise BedrockError(f"unknown operator: {c['op']}", clause=i)
        elif t == "allen":
            if c.get("rel") not in ALLEN:
                raise BedrockError(f"unknown interval relation: {c.get('rel')}", clause=i)
            for k in ("a", "b"):
                j = c.get(k)
                if not isinstance(j, int) or not (0 <= j < len(where)) or j == i:
                    raise BedrockError(f"clause {i} refers to a clause that does not exist",
                                       clause=i)
        elif t == "count":
            if c.get("pred") not in PREDICATES:
                raise BedrockError(f"unknown predicate: {c.get('pred')}", clause=i)
            if c.get("op") not in (">=", "<=", "=="):
                raise BedrockError("count needs one of >=, <=, ==", clause=i)
        elif t == "not":
            # Negation over an open window is a full scan of the store. Requiring a bound is
            # better than quietly taking a minute.
            if not win:
                raise BedrockError("A `NOT` CLAUSE NEEDS A TIME WINDOW", clause=i, hint="window")
            self._validate_clause(c.get("clause") or {}, i, where, win)
        else:
            raise BedrockError(f"unknown clause type: {t}", clause=i)

    # -- estimation --------------------------------------------------------------------------
    def estimate(self, q: dict) -> int:
        """Cheapest bound on the result: the most selective clause caps the join."""
        win = q.get("window")
        counts: list[int] = []
        for c in (q.get("where") or []):
            if c.get("t") != "pred":
                continue
            sql = "SELECT COUNT(*) FROM bd_fact WHERE pred = ?"
            params: list[Any] = [c["pred"]]
            if win:
                sql += " AND valid_from < ? AND (valid_to IS NULL OR valid_to > ?)"
                params += [float(win["to"]) / 1000.0, float(win["from"]) / 1000.0]
            try:
                counts.append(int(self.db.query(sql, params)[0][0]))
            except Exception:
                continue
        if counts:
            return min(counts)
        sql = "SELECT COUNT(*) FROM bd_fact"
        params = []
        if win:
            sql += " WHERE valid_from < ? AND (valid_to IS NULL OR valid_to > ?)"
            params = [float(win["to"]) / 1000.0, float(win["from"]) / 1000.0]
        try:
            return int(self.db.query(sql, params)[0][0])
        except Exception:
            return 0

    # -- compilation -------------------------------------------------------------------------
    def _time_sql(self, q: dict, alias: str = "f") -> tuple[str, list[Any]]:
        parts: list[str] = []
        params: list[Any] = []
        as_of = q.get("asOf")
        if as_of:
            # one parameter is all belief-time travel costs, which is why it is a first-class
            # control in the UI rather than an audit export
            parts.append(f"{alias}.tx_from <= ? AND ({alias}.tx_to IS NULL OR {alias}.tx_to > ?)")
            params += [float(as_of) / 1000.0, float(as_of) / 1000.0]
        else:
            parts.append(f"{alias}.tx_to IS NULL")
        win = q.get("window")
        if win:
            parts.append(f"{alias}.valid_from < ? AND ({alias}.valid_to IS NULL"
                         f" OR {alias}.valid_to > ?)")
            params += [float(win["to"]) / 1000.0, float(win["from"]) / 1000.0]
        return " AND ".join(parts), params

    def _clause_cte(self, c: dict, q: dict) -> tuple[str, list[Any]]:
        t = c["t"]
        tsql, tparams = self._time_sql(q)
        if t == "kind":
            return ("SELECT uid AS subj, first_seen AS vf, last_seen AS vt, NULL AS fid"
                    " FROM bd_entity WHERE kind = ?", [c["kind"]])
        if t == "pred":
            sql = ("SELECT f.subj AS subj, f.valid_from AS vf,"
                   " COALESCE(f.valid_to, 1e18) AS vt, f.id AS fid"
                   f" FROM bd_fact f WHERE f.pred = ? AND {tsql}")
            params: list[Any] = [c["pred"], *tparams]
            if c.get("obj") is not None:
                sql += " AND f.obj = ?"
                params.append(int(c["obj"]))
            if c.get("val") is not None:
                v = c["val"]
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    sql += " AND f.num BETWEEN ? AND ?"
                    params += [float(v[0]), float(v[1])]
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    op = _NUM_OPS.get(str(c.get("op") or "=="), "=")
                    sql += f" AND f.num {op} ?"
                    params.append(float(v))
                else:
                    sql += " AND f.val = ?"
                    params.append(str(v))
            return sql, params
        if t == "count":
            op = c["op"] if c["op"] != "==" else "="
            sql = ("SELECT f.subj AS subj, MIN(f.valid_from) AS vf,"
                   " MAX(COALESCE(f.valid_to, 1e18)) AS vt, NULL AS fid"
                   f" FROM bd_fact f WHERE f.pred = ? AND {tsql}"
                   f" GROUP BY f.subj HAVING COUNT(*) {op} ?")
            return sql, [c["pred"], *tparams, int(c["n"])]
        if t == "not":
            inner_sql, inner_params = self._clause_cte(c["clause"], q)
            sql = ("SELECT e.uid AS subj, e.first_seen AS vf, e.last_seen AS vt, NULL AS fid"
                   f" FROM bd_entity e WHERE e.uid NOT IN (SELECT subj FROM ({inner_sql}))")
            return sql, inner_params
        raise BedrockError(f"clause type {t} has no direct form")

    @staticmethod
    def _allen_sql(rel: str, a: str, b: str) -> str:
        """Allen's interval algebra, computed at query time rather than stored."""
        return {
            "before": f"{a}.vt <= {b}.vf",
            "after": f"{a}.vf >= {b}.vt",
            "during": f"{a}.vf >= {b}.vf AND {a}.vt <= {b}.vt",
            "overlaps": f"{a}.vf < {b}.vt AND {b}.vf < {a}.vt",
            "meets": f"ABS({a}.vt - {b}.vf) < 1.0",
            "starts": f"ABS({a}.vf - {b}.vf) < 1.0",
            "finishes": f"ABS({a}.vt - {b}.vt) < 1.0",
        }[rel]

    def compile(self, q: dict) -> tuple[str, list[Any]]:
        where = q.get("where") or []
        ctes: list[str] = []
        params: list[Any] = []
        direct: list[int] = []
        for i, c in enumerate(where):
            if c["t"] == "allen":
                continue
            sql, p = self._clause_cte(c, q)
            ctes.append(f"c{i} AS ({sql})")
            params += p
            direct.append(i)
        if not direct:
            raise BedrockError("A QUERY NEEDS AT LEAST ONE CONCRETE CLAUSE")
        head = direct[0]
        joins = "".join(f" JOIN c{i} ON c{i}.subj = c{head}.subj" for i in direct[1:])
        conds: list[str] = []
        for c in where:
            if c["t"] != "allen":
                continue
            if c["a"] not in direct or c["b"] not in direct:
                raise BedrockError("an interval relation can only join concrete clauses")
            conds.append(self._allen_sql(c["rel"], f"c{c['a']}", f"c{c['b']}"))
        wsql = (" WHERE " + " AND ".join(conds)) if conds else ""
        limit = int(min(5000, max(1, q.get("limit") or 500)))
        order = {"time": f"c{head}.vf DESC", "confidence": f"c{head}.vf DESC",
                 "duration": f"(c{head}.vt - c{head}.vf) DESC"}.get(q.get("order") or "time",
                                                                    f"c{head}.vf DESC")
        sql = ("WITH " + ", ".join(ctes)
               + f" SELECT DISTINCT c{head}.subj FROM c{head}{joins}{wsql}"
               + f" ORDER BY {order} LIMIT ?")
        params.append(limit)
        return sql, params

    # -- execution ---------------------------------------------------------------------------
    def run(self, q: dict) -> dict:
        t0 = time.time()
        self.validate(q)
        est = self.estimate(q)
        if est > self.MAX_ROWS:
            raise BedrockError(
                f"QUERY TOO BROAD · ESTIMATED {est:,} FACTS",
                hint="window" if not q.get("window") else "clause")
        sql, params = self.compile(q)
        try:
            rows = self.db.query(sql, params)
        except Exception as exc:                                  # pragma: no cover
            log.exception("bedrock query failed")
            raise BedrockError(f"query failed: {exc}") from exc
        uids = [int(r[0]) for r in rows]
        entities = [e for e in (self.store.get_entity(u) for u in uids) if e]
        facts: list[dict] = []
        if uids:
            marks = ",".join("?" for _ in uids)
            tsql, tparams = self._time_sql(q, alias="f")
            fq = ("SELECT f.id, f.subj, f.pred, f.obj, f.val, f.num, f.valid_from, f.valid_to,"
                  " f.tx_from, f.tx_to, f.conf, f.src_kind, f.src_ref, f.model_id,"
                  " f.snapshot_path, f.superseded_by FROM bd_fact f"
                  f" WHERE f.subj IN ({marks}) AND {tsql}"
                  " ORDER BY f.valid_from LIMIT 4000")
            facts = [_fact_row(r) for r in self.db.query(fq, [*uids, *tparams])]
        win = q.get("window") or {}
        return {
            "entities": entities, "facts": facts,
            "truncated": len(uids) >= int(q.get("limit") or 500),
            "estimated": est, "took_ms": round((time.time() - t0) * 1000.0, 1),
            "as_of": q.get("asOf"),
            "window": {"from": win.get("from", 0), "to": win.get("to", time.time() * 1000.0)},
        }


# ── the projector ───────────────────────────────────────────────────────────────────────────

class Projector:
    """Turns the live pipeline into intervals, with the debouncer that makes it affordable."""

    def __init__(self, store: FactStore, config: Any) -> None:
        self.store = store
        self.config = config
        self.open_after = int(self._cfg("open_after", 3))
        self.close_after = int(self._cfg("close_after", 5))
        self.budget = int(self._cfg("max_facts_per_hour", 4000))
        # (subj_uid, pred) -> {val, obj, num, hits, misses, first, last, fid, conf}
        self._pending: dict[tuple[int, str], dict] = {}
        self._hour = 0.0
        self._written = 0
        self._throttled = False

    def _cfg(self, key: str, default: Any) -> Any:
        try:
            return self.config.get(f"bedrock.{key}", default)
        except Exception:
            return default

    # -- write budget ------------------------------------------------------------------------
    def _budget_ok(self) -> bool:
        hour = int(time.time() // 3600)
        if hour != self._hour:
            self._hour = hour
            self._written = 0
            if self._throttled:
                log.info("bedrock: write budget reset, debouncer back to normal")
            self._throttled = False
        if self._written < self.budget:
            return True
        if not self._throttled:
            # raise the thresholds rather than the disk usage, and say so once
            self._throttled = True
            log.warning("bedrock: hit %d facts this hour; raising the debounce thresholds",
                        self.budget)
        return False

    def _thresholds(self) -> tuple[int, int]:
        if self._throttled:
            return self.open_after * 4, self.close_after * 2
        return self.open_after, self.close_after

    # -- the debouncer -----------------------------------------------------------------------
    def offer(self, subj: int, pred: str, *, obj: int | None = None, val: str | None = None,
              num: float | None = None, ts: float, conf: float = 1.0,
              src_kind: str = "detector", src_ref: str | None = None,
              model_id: str | None = None, snapshot: str | None = None) -> None:
        """Offer one observation. Nothing is written until the value has held.

        A person crossing a scene offers `wore(red)` three hundred times; this turns that into
        one row with a start and an end.
        """
        key = (int(subj), pred)
        cur = self._pending.get(key)
        same = (cur is not None and cur["obj"] == obj and cur["val"] == val
                and (num is None or cur["num"] is None
                     or abs((cur["num"] or 0) - num) < max(0.5, abs(num) * 0.08)))
        open_after, _close_after = self._thresholds()
        if cur is None or not same:
            if cur is not None:
                self._close(key, cur, ts)
            self._pending[key] = {
                "obj": obj, "val": val, "num": num, "hits": 1, "misses": 0,
                "first": ts, "last": ts, "fid": None, "conf": conf,
                "src_kind": src_kind, "src_ref": src_ref, "model_id": model_id,
                "snapshot": snapshot,
            }
            return
        cur["hits"] += 1
        cur["misses"] = 0
        cur["last"] = ts
        cur["conf"] = max(cur["conf"], conf)
        if cur["fid"] is None and cur["hits"] >= open_after and self._budget_ok():
            cur["fid"] = self.store.open_fact(
                subj, pred, obj=cur["obj"], val=cur["val"], num=cur["num"],
                valid_from=cur["first"], conf=cur["conf"], src_kind=cur["src_kind"],
                src_ref=cur["src_ref"], model_id=cur["model_id"], snapshot=cur["snapshot"])
            self._written += 1

    def _close(self, key: tuple[int, str], rec: dict, ts: float) -> None:
        if rec.get("fid"):
            # valid_to is the last CONFIRMING observation, not the moment we noticed the absence
            self.store.close_fact(int(rec["fid"]), float(rec["last"]))
        self._pending.pop(key, None)

    def sweep(self, now: float, stale_s: float = 4.0) -> None:
        for key in [k for k, v in self._pending.items() if now - v["last"] > stale_s]:
            self._close(key, self._pending[key], now)

    def flush(self) -> None:
        now = time.time()
        for key in list(self._pending):
            self._close(key, self._pending[key], now)

    # -- live sources ------------------------------------------------------------------------
    def observe_detections(self, cam_name: str, source_id: int | None, dets: list[dict],
                           now: float) -> None:
        """Project one analysis pass. Everything here is interval-compressed."""
        cam_uid = self.store.entity("camera", str(source_id if source_id is not None else "?"),
                                    label=cam_name, ts=now)
        for d in dets:
            if d.get("coasting"):
                continue                      # a held box is not an observation
            did = str(d.get("id") or "")
            if not did:
                continue
            kind = str(d.get("cls") or "object")
            uid = self.store.entity(kind, did, ts=now)
            self.offer(uid, "seen_on", obj=cam_uid, ts=now, conf=float(d.get("conf", 1.0)),
                       src_kind="detector", model_id="yolo")
            attrs = d.get("attrs") or {}
            if attrs.get("upper_color"):
                self.offer(uid, "wore", val=str(attrs["upper_color"]), ts=now, conf=0.7,
                           src_kind="detector", model_id="palette")
            if attrs.get("height_cm"):
                self.offer(uid, "estimated_height", num=float(attrs["height_cm"]), ts=now,
                           conf=0.5, src_kind="detector", model_id="stature")
            if d.get("plate"):
                self.offer(uid, "has_plate", val=str(d["plate"]), ts=now, conf=0.85,
                           src_kind="anpr", model_id="anpr")
            if d.get("subtype"):
                self.offer(uid, "is_subtype", val=str(d["subtype"]), ts=now, conf=0.8,
                           src_kind="detector")
            if d.get("bodytype"):
                self.offer(uid, "is_bodytype", val=str(d["bodytype"]), ts=now, conf=0.6,
                           src_kind="detector", model_id="clip")
            if d.get("make"):
                self.offer(uid, "has_make", val=str(d["make"]), ts=now, conf=0.6,
                           src_kind="detector", model_id="vit")
            if d.get("speed") is not None:
                self.offer(uid, "moving_at", num=float(d["speed"]), ts=now, conf=0.4,
                           src_kind="detector")
            it = d.get("intent") or {}
            if it.get("intent"):
                self.offer(uid, "intent", val=str(it["intent"]), ts=now,
                           conf=float(it.get("confidence", 0.5)), src_kind="pose")
            cf = d.get("conformity") or {}
            if cf.get("p") is not None and cf.get("state") != "unjudged":
                self.offer(uid, "conformity", num=float(cf["p"]), ts=now, conf=0.8,
                           src_kind="grain", model_id="grain-a")
        self.sweep(now)

    def observe_alert(self, alert: dict, now: float) -> None:
        cam = str(alert.get("cam") or "?")
        cam_uid = self.store.entity("camera", cam, label=cam, ts=now)
        aid = f"{alert.get('type')}@{int(now)}"
        uid = self.store.entity("alert", aid, label=str(alert.get("type")),
                                snapshot=alert.get("snapshot"), ts=now)
        # an alert is a point event, so it is written directly rather than debounced
        self.store.open_fact(uid, "alerted", val=str(alert.get("type")), valid_from=now,
                             conf=1.0, src_kind="rule", src_ref=str(alert.get("threat") or ""),
                             snapshot=alert.get("snapshot"))
        self.store.open_fact(uid, "seen_on", obj=cam_uid, valid_from=now, conf=1.0,
                             src_kind="rule")

    def observe_divergence(self, cam: str, div: dict, now: float) -> None:
        cam_uid = self.store.entity("camera", str(cam), label=str(cam), ts=now)
        self.store.open_fact(cam_uid, "diverged", num=float(div.get("peak_sigma", 0.0)),
                             valid_from=now, conf=0.9, src_kind="dream",
                             src_ref=str(div.get("id") or ""), model_id="dream-a",
                             snapshot=div.get("snapshot"))

    def observe_subject_merge(self, det_ref: str, subject_uid: int, now: float) -> None:
        """A roster merge is exactly the case transaction time exists for."""
        a = self.store.entity("person", det_ref, ts=now)
        b = self.store.entity("subject", str(subject_uid), ts=now)
        self.store.open_fact(a, "same_as", obj=b, valid_from=now, conf=0.8, src_kind="reid",
                             model_id="osnet")

    # -- backfill ----------------------------------------------------------------------------
    def backfill(self, db: Any, progress: dict) -> None:
        """Project the tables that already hold years of history.

        Resumable and reported: a first run on a busy install has real work to do, and a
        progress bar with named phases is the difference between waiting and wondering.
        """
        phases = [("EVENTS", self._backfill_events), ("ALERTS", self._backfill_alerts),
                  ("SIGHTINGS", self._backfill_sightings), ("SUBJECTS", self._backfill_subjects)]
        progress["total"] = len(phases)
        for i, (name, fn) in enumerate(phases):
            progress["phase"] = name
            progress["done"] = i
            try:
                fn(db, progress)
            except Exception:
                log.exception("bedrock backfill phase %s failed", name)
        progress["phase"] = "DONE"
        progress["done"] = len(phases)
        progress["running"] = False

    def _backfill_events(self, db: Any, progress: dict) -> None:
        rows = db.query("SELECT id, ts, type, source_id, label, snapshot_path FROM events"
                        " ORDER BY ts LIMIT 20000")
        for (eid, ts, etype, sid, label, snap) in rows:
            uid = self.store.entity("event", f"event:{eid}", label=str(etype), snapshot=snap,
                                    ts=float(ts))
            self.store.open_fact(uid, "alerted", val=str(etype), valid_from=float(ts), conf=1.0,
                                 src_kind="rule", src_ref=f"event:{eid}", snapshot=snap)
            if sid is not None:
                cam = self.store.entity("camera", str(sid), ts=float(ts))
                self.store.open_fact(uid, "seen_on", obj=cam, valid_from=float(ts), conf=1.0,
                                     src_kind="rule")
            progress["facts"] = progress.get("facts", 0) + 2

    def _backfill_alerts(self, db: Any, progress: dict) -> None:
        rows = db.query("SELECT id, ts, event_type, source_id, severity, snapshot_path"
                        " FROM alerts ORDER BY ts LIMIT 20000")
        for (aid, ts, etype, sid, sev, snap) in rows:
            uid = self.store.entity("alert", f"alert:{aid}", label=str(etype), snapshot=snap,
                                    ts=float(ts))
            self.store.open_fact(uid, "alerted", val=str(etype), valid_from=float(ts), conf=1.0,
                                 src_kind="rule", src_ref=f"alert:{aid}", snapshot=snap)
            progress["facts"] = progress.get("facts", 0) + 1

    def _backfill_sightings(self, db: Any, progress: dict) -> None:
        rows = db.query("SELECT id, subject_id, source_id, cam, ts, snapshot_path"
                        " FROM sightings ORDER BY ts LIMIT 40000")
        for (gid, subj, sid, cam, ts, snap) in rows:
            uid = self.store.entity("subject", str(subj), ts=float(ts))
            camu = self.store.entity("camera", str(sid if sid is not None else cam),
                                     label=str(cam or ""), ts=float(ts))
            self.store.open_fact(uid, "seen_on", obj=camu, valid_from=float(ts), conf=0.9,
                                 src_kind="reid", src_ref=f"sighting:{gid}", snapshot=snap)
            progress["facts"] = progress.get("facts", 0) + 1

    def _backfill_subjects(self, db: Any, progress: dict) -> None:
        rows = db.query("SELECT id, cls, label, first_seen, last_seen, plate, attrs, watched,"
                        " flags, snapshot_path FROM subjects LIMIT 20000")
        for (sid, cls, label, first, last, plate, attrs, watched, flags, snap) in rows:
            uid = self.store.entity("subject", str(sid), label=label, snapshot=snap,
                                    ts=float(first))
            if plate:
                self.store.open_fact(uid, "has_plate", val=str(plate), valid_from=float(first),
                                     conf=0.85, src_kind="anpr")
            if watched:
                self.store.open_fact(uid, "watched", val="true", valid_from=float(first),
                                     conf=1.0, src_kind="operator")
            try:
                for f in json.loads(flags or "[]"):
                    self.store.open_fact(uid, "flagged", val=str(f), valid_from=float(first),
                                         conf=1.0, src_kind="reid")
            except Exception:
                pass
            try:
                a = json.loads(attrs or "{}")
                if a.get("upper_color"):
                    self.store.open_fact(uid, "wore", val=str(a["upper_color"]),
                                         valid_from=float(first), conf=0.6, src_kind="detector")
            except Exception:
                pass
            progress["facts"] = progress.get("facts", 0) + 1


def vocabulary() -> dict:
    """What the chip builder and the LLM planner are allowed to say."""
    return {
        "version": VOCAB_VERSION,
        "predicates": [{"pred": k, **v} for k, v in PREDICATES.items()],
        "kinds": list(KINDS),
    }


def suggest(store: FactStore, limit: int = 3) -> list[dict]:
    """Starter queries built from what is actually in the store, so an empty result is never a
    dead end."""
    out: list[dict] = []
    try:
        rows = store.db.query(
            "SELECT pred, COUNT(*) c FROM bd_fact WHERE tx_to IS NULL"
            " GROUP BY pred ORDER BY c DESC LIMIT ?", (int(limit),))
    except Exception:
        return out
    day = 86_400_000.0
    now = time.time() * 1000.0
    for pred, n in rows:
        meta = PREDICATES.get(pred)
        if not meta:
            continue
        out.append({
            "label": f"ANY {meta['family'].upper()} · {meta['label']} · LAST 24H",
            "query": {"select": "entity", "where": [{"t": "pred", "pred": pred}],
                      "window": {"from": now - day, "to": now}, "limit": 200},
            "count": int(n),
        })
    return out
