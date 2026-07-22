# tests/test_cases_db.py
from pathlib import Path
from typing import Iterator

import pytest

from storage.database import Case, CaseTarget, Database


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "c.db")
    yield d
    d.close()


def _add_tracklet(db: Database, track_id: int = 1, first_ts: float = 100.0) -> int:
    return db.add_tracklet(1, track_id, first_ts, first_ts)


def test_add_case_and_list_and_update(db: Database) -> None:
    cid = db.add_case("Envelope Case", threat_level="medium", notes="ilk not")
    cases = db.list_cases()
    assert len(cases) == 1
    assert isinstance(cases[0], Case)
    assert cases[0].id == cid
    assert cases[0].name == "Envelope Case"
    assert cases[0].threat_level == "medium"
    assert cases[0].notes == "ilk not"

    db.update_case(cid, "Envelope Case 2", "high", "updated note")
    updated = db.list_cases()[0]
    assert updated.name == "Envelope Case 2"
    assert updated.threat_level == "high"
    assert updated.notes == "updated note"


def test_add_case_default_threat_level_and_notes(db: Database) -> None:
    cid = db.add_case("Simple Case")
    case = db.list_cases()[0]
    assert case.id == cid
    assert case.threat_level == "low"
    assert case.notes == ""


def test_add_case_target_pins_tracklet_and_lists_with_alias(db: Database) -> None:
    tid = _add_tracklet(db)
    cid = db.add_case("Case A")
    target_id = db.add_case_target(cid, tid, alias="Suspect 1")

    tracklet = db.get_tracklet(tid)
    assert tracklet is not None
    assert tracklet.pinned == 1

    targets = db.list_case_targets(cid)
    assert len(targets) == 1
    assert isinstance(targets[0], CaseTarget)
    assert targets[0].id == target_id
    assert targets[0].case_id == cid
    assert targets[0].tracklet_id == tid
    assert targets[0].alias == "Suspect 1"


def test_remove_case_target_unpins_when_no_longer_referenced(db: Database) -> None:
    tid = _add_tracklet(db)
    cid = db.add_case("Case A")
    target_id = db.add_case_target(cid, tid)

    db.remove_case_target(target_id)

    assert db.get_tracklet(tid).pinned == 0
    assert db.list_case_targets(cid) == []


def test_remove_case_target_keeps_pin_if_referenced_by_another_case(db: Database) -> None:
    tid = _add_tracklet(db)
    case1 = db.add_case("Case A")
    case2 = db.add_case("Case B")
    target1 = db.add_case_target(case1, tid)
    db.add_case_target(case2, tid)

    db.remove_case_target(target1)

    assert db.get_tracklet(tid).pinned == 1
    assert db.list_case_targets(case1) == []
    assert len(db.list_case_targets(case2)) == 1


def test_delete_case_removes_targets_and_unpins_unreferenced_tracklet(db: Database) -> None:
    tid = _add_tracklet(db)
    cid = db.add_case("Case A")
    db.add_case_target(cid, tid)

    db.delete_case(cid)

    assert db.list_cases() == []
    assert db.list_case_targets(cid) == []
    assert db.get_tracklet(tid).pinned == 0


def test_delete_case_keeps_pin_if_tracklet_referenced_by_other_case(db: Database) -> None:
    tid = _add_tracklet(db)
    case1 = db.add_case("Case A")
    case2 = db.add_case("Case B")
    db.add_case_target(case1, tid)
    db.add_case_target(case2, tid)

    db.delete_case(case1)

    assert db.get_tracklet(tid).pinned == 1
    assert len(db.list_case_targets(case2)) == 1
