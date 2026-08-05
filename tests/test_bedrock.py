"""BEDROCK: the bitemporal fact store.

Two tests here decide whether the feature is shippable at all: interval compression (without it
the disk is gone by Friday) and as-of correctness across a supersession (without it the second
time axis is decoration). The rest guard the compiler against running things it should refuse.
"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pytest

from server.bedrock import (
    ALLEN, KINDS, PREDICATES, VOCAB_VERSION, BedrockError, FactStore, Projector, QueryCompiler,
    suggest, vocabulary,
)
from storage.database import Database


class _Cfg:
    def __init__(self, **kw) -> None:
        self.kw = kw

    def get(self, key, default=None):
        return self.kw.get(key, default)


def _stack(**cfg):
    base = {"bedrock.open_after": 3, "bedrock.close_after": 5}
    base.update(cfg)
    db = Database(Path(tempfile.mkdtemp()) / "b.db")
    store = FactStore(db)
    return db, store, Projector(store, _Cfg(**base)), QueryCompiler(store)


def _person(colour: str = "red") -> list[dict]:
    return [{"id": "TK_1.7", "cls": "person", "conf": 0.9, "bbox": [0.4, 0.4, 0.1, 0.3],
             "attrs": {"upper_color": colour, "height_cm": 178}}]


# ── interval compression ────────────────────────────────────────────────────────────────────

def test_a_crossing_produces_a_handful_of_facts_not_hundreds() -> None:
    """The decision the whole store rests on. Naive per-observation writing at analysis rate is
    >150k rows an hour."""
    _db, store, pj, _qc = _stack()
    t = time.time()
    for i in range(200):
        pj.observe_detections("NORTH GATE", 1, _person(), t + i * 0.33)
    pj.flush()
    assert store.stats()["facts"] <= 8


def test_nothing_is_written_until_the_value_holds() -> None:
    _db, store, pj, _qc = _stack(**{"bedrock.open_after": 5})
    t = time.time()
    for i in range(3):
        pj.observe_detections("CAM", 1, _person(), t + i * 0.3)
    assert store.stats()["facts"] == 0     # three observations is not yet a fact
    for i in range(3, 8):
        pj.observe_detections("CAM", 1, _person(), t + i * 0.3)
    assert store.stats()["facts"] > 0


def test_a_changed_value_closes_the_old_interval_and_opens_a_new_one() -> None:
    _db, store, pj, _qc = _stack()
    t = time.time()
    for i in range(10):
        pj.observe_detections("CAM", 1, _person("red"), t + i * 0.3)
    for i in range(10, 20):
        pj.observe_detections("CAM", 1, _person("blue"), t + i * 0.3)
    pj.flush()
    uid = store.entity("person", "TK_1.7")
    wore = [f for f in store.facts_for(uid) if f["pred"] == "wore"]
    assert {f["val"] for f in wore} == {"red", "blue"}
    red = next(f for f in wore if f["val"] == "red")
    assert red["valid_to"] is not None      # the first interval is closed, not left dangling


def test_valid_to_is_the_last_confirming_observation() -> None:
    """Not the moment we noticed the absence: the subject was there until they were last seen."""
    _db, store, pj, _qc = _stack()
    t = time.time()
    for i in range(10):
        pj.observe_detections("CAM", 1, _person(), t + i * 0.3)
    last_seen = t + 9 * 0.3
    pj.sweep(t + 60)
    uid = store.entity("person", "TK_1.7")
    f = next(x for x in store.facts_for(uid) if x["pred"] == "wore")
    assert f["valid_to"] == pytest.approx(last_seen * 1000.0, abs=500)


def test_a_coasted_box_is_not_an_observation() -> None:
    _db, store, pj, _qc = _stack()
    t = time.time()
    held = [dict(_person()[0], coasting=True)]
    for i in range(20):
        pj.observe_detections("CAM", 1, held, t + i * 0.3)
    assert store.stats()["facts"] == 0


def test_the_write_budget_raises_thresholds_instead_of_the_disk_usage() -> None:
    _db, store, pj, _qc = _stack(**{"bedrock.max_facts_per_hour": 2})
    t = time.time()
    for i in range(60):
        d = [dict(_person()[0], id=f"TK_1.{i}")]
        for k in range(6):
            pj.observe_detections("CAM", 1, d, t + i * 3 + k * 0.3)
    pj.flush()
    assert pj._throttled
    assert store.stats()["facts"] <= 40     # bounded, not unbounded


# ── bitemporality ───────────────────────────────────────────────────────────────────────────

def _one_person(pj: Projector, t: float) -> None:
    for i in range(10):
        pj.observe_detections("NORTH GATE", 1, _person(), t + i * 0.3)
    pj.flush()


def _q(t: float, **kw) -> dict:
    q = {"select": "entity",
         "where": [{"t": "kind", "kind": "person"}, {"t": "pred", "pred": "wore", "val": "red"}],
         "window": {"from": (t - 60) * 1000, "to": (t + 60) * 1000}, "limit": 50}
    q.update(kw)
    return q


def test_as_of_before_we_believed_it_returns_nothing() -> None:
    """Transaction time is when we came to believe a thing, and one parameter is all it costs."""
    _db, _store, pj, qc = _stack()
    t = time.time()
    _one_person(pj, t)
    assert len(qc.run(_q(t))["entities"]) == 1
    assert len(qc.run(_q(t, asOf=(t - 30) * 1000))["entities"]) == 0


def test_a_supersession_hides_the_belief_now_but_not_in_the_past() -> None:
    """The load-bearing property: a correction never deletes, so 'what did we believe last
    Tuesday' stays answerable."""
    _db, store, pj, qc = _stack()
    t = time.time()
    _one_person(pj, t)
    fact = next(f for f in qc.run(_q(t))["facts"] if f["pred"] == "wore")
    believed_at = time.time()
    time.sleep(0.02)
    store.supersede(fact["id"])
    assert not [f for f in qc.run(_q(t))["facts"] if f["id"] == fact["id"]]
    back = qc.run(_q(t, asOf=believed_at * 1000))
    assert [f for f in back["facts"] if f["id"] == fact["id"]]


def test_provenance_carries_the_model_and_the_source() -> None:
    """A fact without its provenance is a rumour."""
    _db, store, pj, _qc = _stack()
    t = time.time()
    _one_person(pj, t)
    uid = store.entity("person", "TK_1.7")
    f = next(x for x in store.facts_for(uid) if x["pred"] == "wore")
    p = store.provenance(f["id"])
    assert p["fact"]["src_kind"] == "detector"
    assert p["fact"]["model_id"] == "palette"
    assert 0.0 < p["fact"]["conf"] <= 1.0


def test_lineage_walks_back_through_replacements() -> None:
    _db, store, _pj, _qc = _stack()
    t = time.time()
    uid = store.entity("person", "X", ts=t)
    old = store.open_fact(uid, "wore", val="red", valid_from=t)
    new = store.open_fact(uid, "wore", val="crimson", valid_from=t)
    store.supersede(old, replacement=new)
    lin = store.provenance(new)["lineage"]
    assert [f["val"] for f in lin] == ["red"]


# ── the compiler ────────────────────────────────────────────────────────────────────────────

def test_an_unbounded_query_is_refused() -> None:
    _db, _store, _pj, qc = _stack()
    with pytest.raises(BedrockError, match="UNBOUNDED"):
        qc.run({"select": "entity", "where": []})


def test_negation_requires_a_window() -> None:
    """`NOT present_in(zone)` over an open window is a full scan; requiring a bound is better
    than quietly taking a minute."""
    _db, _store, _pj, qc = _stack()
    with pytest.raises(BedrockError, match="WINDOW"):
        qc.run({"select": "entity",
                "where": [{"t": "not", "clause": {"t": "pred", "pred": "wore"}}]})


def test_unknown_predicates_and_kinds_are_refused() -> None:
    _db, _store, _pj, qc = _stack()
    t = time.time()
    with pytest.raises(BedrockError, match="unknown predicate"):
        qc.run(_q(t, where=[{"t": "pred", "pred": "telepathy"}]))
    with pytest.raises(BedrockError, match="unknown entity kind"):
        qc.run(_q(t, where=[{"t": "kind", "kind": "ghost"}]))


def test_an_allen_clause_pointing_nowhere_is_refused() -> None:
    _db, _store, _pj, qc = _stack()
    t = time.time()
    with pytest.raises(BedrockError, match="does not exist"):
        qc.run(_q(t, where=[{"t": "pred", "pred": "wore"},
                            {"t": "allen", "rel": "before", "a": 0, "b": 9}]))


def test_a_query_that_would_return_too_much_is_refused_with_a_hint() -> None:
    """Refusing with a named cause beats taking a minute and returning everything."""
    _db, _store, pj, qc = _stack()
    t = time.time()
    _one_person(pj, t)
    qc.MAX_ROWS = 0                          # anything real now exceeds the budget
    with pytest.raises(BedrockError, match="TOO BROAD") as ei:
        qc.run(_q(t))
    assert ei.value.hint in ("window", "clause")


def test_a_count_clause_filters_by_repetition() -> None:
    _db, store, pj, qc = _stack()
    t = time.time()
    for visit in range(4):
        for i in range(8):
            pj.observe_detections("CAM", 1, _person(), t + visit * 100 + i * 0.3)
        pj.sweep(t + visit * 100 + 60)
    pj.flush()
    q = {"select": "entity",
         "where": [{"t": "count", "pred": "seen_on", "op": ">=", "n": 3}],
         "window": {"from": (t - 60) * 1000, "to": (t + 1000) * 1000}, "limit": 50}
    assert len(qc.run(q)["entities"]) == 1
    q["where"][0]["n"] = 99
    assert len(qc.run(q)["entities"]) == 0


def test_allen_before_orders_two_clauses() -> None:
    _db, store, _pj, qc = _stack()
    t = time.time()
    uid = store.entity("person", "P", ts=t)
    early = store.open_fact(uid, "entered", val="gate", valid_from=t)
    store.close_fact(early, t + 5)
    late = store.open_fact(uid, "exited", val="gate", valid_from=t + 10)
    store.close_fact(late, t + 15)
    base = {"select": "entity", "window": {"from": (t - 60) * 1000, "to": (t + 60) * 1000}}
    ok = dict(base, where=[{"t": "pred", "pred": "entered"}, {"t": "pred", "pred": "exited"},
                           {"t": "allen", "rel": "before", "a": 0, "b": 1}])
    assert len(qc.run(ok)["entities"]) == 1
    backwards = dict(base, where=[{"t": "pred", "pred": "entered"}, {"t": "pred", "pred": "exited"},
                                  {"t": "allen", "rel": "after", "a": 0, "b": 1}])
    assert len(qc.run(backwards)["entities"]) == 0


def test_a_numeric_range_clause_works() -> None:
    _db, store, pj, qc = _stack()
    t = time.time()
    _one_person(pj, t)
    q = _q(t, where=[{"t": "pred", "pred": "estimated_height", "val": [170, 185]}])
    assert len(qc.run(q)["entities"]) == 1
    q = _q(t, where=[{"t": "pred", "pred": "estimated_height", "val": [190, 210]}])
    assert len(qc.run(q)["entities"]) == 0


# ── erasure, vocabulary, suggestions ────────────────────────────────────────────────────────

def test_purging_a_subject_destroys_every_trace() -> None:
    """A fact store profiles far harder than a video archive. Erasure is not optional."""
    _db, store, pj, qc = _stack()
    t = time.time()
    _one_person(pj, t)
    uid = store.entity("person", "TK_1.7")
    res = store.purge_subject(uid)
    assert res["facts"] > 0 and res["entities"] == 1
    assert store.facts_for(uid) == []
    assert store.get_entity(uid) is None


def test_the_vocabulary_is_closed_and_versioned() -> None:
    v = vocabulary()
    assert v["version"] == VOCAB_VERSION
    assert {p["pred"] for p in v["predicates"]} == set(PREDICATES)
    assert set(v["kinds"]) == set(KINDS)
    assert all(p["object"] in ("entity", "literal", "number") for p in v["predicates"])
    assert len(ALLEN) == 7


def test_suggestions_come_from_what_is_actually_stored() -> None:
    """An empty result must never be a dead end."""
    _db, store, pj, _qc = _stack()
    t = time.time()
    _one_person(pj, t)
    s = suggest(store, 3)
    assert s and all("query" in x and "label" in x for x in s)


def test_backfill_projects_the_existing_tables() -> None:
    db, store, pj, _qc = _stack()
    now = time.time()
    db.execute("INSERT INTO events (ts, type, source_id, label, metadata)"
               " VALUES (?,?,?,?,?)", (now, "LOITERING", 1, "person", "{}"))
    db.execute("INSERT INTO alerts (ts, rule_name, event_type, source_id, severity, summary)"
               " VALUES (?,?,?,?,?,?)", (now, "r", "WEAPON", 1, "critical", "s"))
    progress: dict = {"running": True}
    pj.backfill(db, progress)
    assert progress["phase"] == "DONE" and progress["running"] is False
    assert store.stats()["facts"] >= 3
