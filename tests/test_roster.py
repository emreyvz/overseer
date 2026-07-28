from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from server.roster import RosterHarvester, SessionRoster


class _FakeSnaps:
    def __init__(self, d: Path) -> None:
        self._d = d
        self._n = 0

    def save(self, crop, prefix: str = "s") -> Path:
        self._n += 1
        p = self._d / f"{prefix}_{self._n}.jpg"
        cv2.imwrite(str(p), crop)
        return p


def _crop(w=90, h=60, v=180):
    return np.full((h, w, 3), v, np.uint8)


def _emb(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    v = rng.standard_normal(16).astype(np.float32)
    return v / np.linalg.norm(v)


def _roster(tmp_path: Path) -> SessionRoster:
    return SessionRoster(_FakeSnaps(tmp_path), tmp_path, dedup_threshold=0.82)


def test_creates_anonymous_entry(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    rid = r.observe_reid("person", _crop(), _emb(1), now=0.0, attrs={"upper_color": "red"})
    assert rid == "P-001"
    e = r.get(rid)
    assert e["cls"] == "person"
    assert e["snapshot"].startswith("/snapshots/")
    assert e["attrs"]["upper_color"] == "red"
    assert e["obs"] == 1


def test_trail_records_cameras_over_time(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    emb = _emb(1)
    r.observe_reid("person", _crop(), emb, now=0.0, cam="Gate")     # first seen here
    r.observe_reid("person", _crop(), emb, now=5.0, cam="Gate")     # still Gate
    r.observe_reid("person", _crop(), emb, now=9.0, cam="Lobby")    # moved to Lobby
    e = r.get("P-001")
    assert e["first_cam"] == "Gate"
    assert e["cam"] == "Lobby"                                       # last seen
    trail = e["trail"]
    assert [t["cam"] for t in trail] == ["Gate", "Lobby"]            # ordered by first sighting
    gate = trail[0]
    assert gate["count"] == 2 and gate["first"] == 0.0 and gate["last"] == 5000.0


def test_reid_dedups_same_subject(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    emb = _emb(1)
    r.observe_reid("person", _crop(), emb, now=0.0)
    r.observe_reid("person", _crop(), emb, now=1.0)          # same embedding -> merge
    r.observe_reid("person", _crop(), emb * 1.0, now=2.0)    # still same
    assert len(r.list()) == 1
    assert r.get("P-001")["obs"] == 3


def test_reid_separates_different_subjects(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    r.observe_reid("person", _crop(), _emb(1), now=0.0)
    r.observe_reid("person", _crop(), _emb(2), now=1.0)      # different embedding -> new id
    ids = sorted(x["id"] for x in r.list())
    assert ids == ["P-001", "P-002"]


def test_class_kept_separate(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    r.observe_reid("person", _crop(), _emb(1), now=0.0)
    r.observe_reid("vehicle", _crop(), _emb(1), now=1.0, plate="34ABC123")  # same emb, other class
    assert {x["id"] for x in r.list()} == {"P-001", "V-001"}
    assert r.get("V-001")["plate"] == "34ABC123"


def test_list_sorted_and_hides_photoless(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    r.observe_reid("person", _crop(), _emb(1), now=1.0)
    r.observe_reid("vehicle", _crop(), _emb(2), now=2.0)
    r.observe_reid("person", _crop(w=10, h=10), _emb(3), now=3.0)   # too small -> no photo
    ids = [x["id"] for x in r.list()]
    assert ids == ["V-001", "P-001"]           # newest first; the photoless one is hidden


def test_cutout_png_without_seg(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    r.observe_reid("person", _crop(), _emb(1), now=0.0)
    png = r.cutout_png("P-001")
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert r.cutout_png("missing") is None


def test_no_embedding_never_merges(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    r.observe_reid("person", _crop(), None, now=0.0)
    r.observe_reid("person", _crop(), None, now=1.0)   # no embedding -> can't dedup -> separate
    assert len(r.list()) == 2


@dataclass
class _Det:
    bbox: tuple
    category: str
    confidence: float = 0.9


@dataclass
class _Src:
    name: str


def test_harvester_scan_populates_from_any_camera(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    frame = np.full((240, 240, 3), 90, np.uint8)
    h = RosterHarvester(
        r,
        sources_fn=lambda: [_Src("cam-A")],
        frame_fn=lambda s: frame,
        detect_fn=lambda f: [_Det((10, 10, 100, 150), "person")],
        embed_fn=lambda crop, cls: _emb(1),
        cat_to_cls={"person": "person", "vehicle": "vehicle"},
    )
    h.stop()                     # don't run the thread; drive _scan directly
    h._scan(_Src("cam-A"))
    items = r.list()
    assert len(items) == 1
    assert items[0]["cls"] == "person"
    assert items[0]["cam"] == "cam-A"


def test_harvester_dedups_across_scans(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    frame = np.full((240, 240, 3), 90, np.uint8)
    h = RosterHarvester(
        r, sources_fn=lambda: [_Src("cam-A")], frame_fn=lambda s: frame,
        detect_fn=lambda f: [_Det((10, 10, 100, 150), "person")],
        embed_fn=lambda crop, cls: _emb(7),      # same subject every scan
        cat_to_cls={"person": "person"},
    )
    h.stop()
    h._scan(_Src("cam-A"))
    h._scan(_Src("cam-A"))
    assert len(r.list()) == 1                    # same embedding -> one identity
