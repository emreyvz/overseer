from pathlib import Path

import cv2
import numpy as np

from server.roster import SessionRoster


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


def _roster(tmp_path: Path) -> SessionRoster:
    return SessionRoster(_FakeSnaps(tmp_path), tmp_path)


def test_creates_entry_with_snapshot(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    r.observe("TK.1", "person", _crop(), now=0.0, attrs={"upper_color": "red"})
    e = r.get("TK.1")
    assert e is not None
    assert e["cls"] == "person"
    assert e["snapshot"] and e["snapshot"].startswith("/snapshots/")
    assert e["attrs"]["upper_color"] == "red"
    assert e["obs"] == 1


def test_dedup_same_track(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    r.observe("TK.1", "person", _crop(), now=0.0)
    r.observe("TK.1", "person", _crop(), now=0.5)
    r.observe("TK.1", "person", _crop(), now=1.0)
    assert len([x for x in r.list() if x["id"] == "TK.1"]) == 1
    assert r.get("TK.1")["obs"] == 3


def test_vehicle_plate_recorded(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    r.observe("TK.9", "vehicle", _crop(), now=0.0)
    r.observe("TK.9", "vehicle", _crop(), now=2.0, plate="34ABC123")
    assert r.get("TK.9")["plate"] == "34ABC123"


def test_list_sorted_and_filters_photoless(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    r.observe("TK.1", "person", _crop(), now=1.0)
    r.observe("TK.2", "vehicle", _crop(), now=2.0)
    r.observe("TK.3", "person", _crop(w=10, h=10), now=3.0)  # too small -> no photo -> hidden
    ids = [x["id"] for x in r.list()]
    assert ids == ["TK.2", "TK.1"]           # newest first, TK.3 filtered (no snapshot)


def test_cutout_png_without_seg(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    r.observe("TK.1", "person", _crop(), now=0.0)
    png = r.cutout_png("TK.1")
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"   # PNG signature
    assert r.cutout_png("missing") is None


def test_tiny_crops_never_photographed(tmp_path: Path) -> None:
    r = _roster(tmp_path)
    r.observe("TK.1", "person", _crop(w=10, h=10), now=0.0)
    assert r.get("TK.1")["snapshot"] is None
