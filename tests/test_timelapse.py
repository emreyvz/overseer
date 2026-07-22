import threading
import time
from pathlib import Path
from typing import Callable, Iterator

import cv2
import numpy as np
import pytest

from camera.frame_buffer import Frame
from core.config import Config, load_config
from storage import timelapse as timelapse_module
from storage.database import Database
from storage.timelapse import TimelapseWriter


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "timelapse:\n  enabled: true\n  dir: "
        + str(tmp_path / "tl").replace("\\", "/")
        + "\n  sample_interval_seconds: 0.05\n  fps: 30\n",
        encoding="utf-8",
    )
    return load_config(p)


@pytest.fixture()
def rotating_config(tmp_path: Path) -> Config:
    p = tmp_path / "c_rotate.yaml"
    p.write_text(
        "timelapse:\n  enabled: true\n  dir: "
        + str(tmp_path / "tl_rotate").replace("\\", "/")
        + "\n  sample_interval_seconds: 0.05\n  fps: 30\n"
        "  max_segment_seconds: 0.5\n",
        encoding="utf-8",
    )
    return load_config(p)


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def frame(i: int) -> Frame:
    return Frame(image=np.full((32, 48, 3), (i * 9) % 255, dtype=np.uint8),
                 timestamp=time.time(), seq=i)


def wait_until(cond: Callable[[], bool], timeout: float = 6.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_samples_and_flushes(config: Config, db: Database) -> None:
    tl = TimelapseWriter(config, db)
    tl.source_id = 1
    tl.start()
    try:
        for i in range(30):
            tl.offer(frame(i))
            time.sleep(0.03)
        tl.flush_now()
        assert wait_until(lambda: len(db.list_recordings(kind="timelapse")) >= 1)
    finally:
        tl.stop()
        tl.join(timeout=5)
    rec = db.list_recordings(kind="timelapse")[0]
    path = Path(rec.path)
    assert path.exists() and path.stat().st_size > 0
    cap = cv2.VideoCapture(str(path))
    try:
        frames = 0
        while cap.read()[0]:
            frames += 1
        assert frames >= 1
    finally:
        cap.release()


def test_finalize_attributes_source_id_captured_at_open(
    config: Config, db: Database
) -> None:
    """A timelapse opened under source_id=1 must finalize as source_id=1 even
    if source_id gets reassigned (e.g. by a source switch) before flush."""
    tl = TimelapseWriter(config, db)
    tl.source_id = 1
    tl.start()
    try:
        tl.offer(frame(0))
        assert wait_until(lambda: tl._writer is not None, timeout=2.0)
        tl.source_id = 2  # reassigned mid-flight, as connect_source() does
        tl.flush_now()
        assert wait_until(lambda: len(db.list_recordings(kind="timelapse")) >= 1)
    finally:
        tl.stop()
        tl.join(timeout=5)
    rec = db.list_recordings(kind="timelapse")[0]
    assert rec.source_id == 1


def test_open_segment_twice_gets_distinct_paths(config: Config, db: Database) -> None:
    tl = TimelapseWriter(config, db)
    tl.source_id = 1
    img = frame(0).image
    tl._write(img)
    first_path = tl._writer_path
    tl._finalize(time.time())
    tl._write(img)
    second_path = tl._writer_path
    tl._finalize(time.time())
    assert first_path is not None and second_path is not None
    assert first_path != second_path


def test_rotates_on_max_segment_seconds(rotating_config: Config, db: Database) -> None:
    tl = TimelapseWriter(rotating_config, db)
    tl.source_id = 1
    tl.start()
    try:
        deadline = time.time() + 1.5
        i = 0
        while time.time() < deadline:
            tl.offer(frame(i))
            i += 1
            time.sleep(0.05)
        # No flush_now() call anywhere: rotation alone must produce multiple
        # finalized segments over ~1.5s with a 0.5s max_segment_seconds.
        assert wait_until(
            lambda: len(db.list_recordings(kind="timelapse")) >= 2, timeout=3.0
        )
    finally:
        tl.stop()
        tl.join(timeout=5)
    recs = db.list_recordings(kind="timelapse")
    assert len(recs) >= 2
    for rec in recs:
        path = Path(rec.path)
        assert path.exists() and path.stat().st_size > 0


def test_disabled_no_output(config: Config, db: Database) -> None:
    config.set("timelapse.enabled", False)
    tl = TimelapseWriter(config, db)
    tl.start()
    try:
        for i in range(20):
            tl.offer(frame(i))
            time.sleep(0.02)
        tl.flush_now()
        time.sleep(0.2)
    finally:
        tl.stop()
        tl.join(timeout=5)
    assert db.list_recordings(kind="timelapse") == []


def test_stop_without_start_is_safe(config: Config, db: Database) -> None:
    TimelapseWriter(config, db).stop()


def test_codec_open_exception_disables_gracefully(
    config: Config, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raising open_video_writer() must not crash the writer thread; it must
    be caught and the writer disabled gracefully (mirrors recorder._open_clip)."""

    def raising_open(*args: object, **kwargs: object) -> None:
        raise OSError("boom")

    monkeypatch.setattr(timelapse_module, "open_video_writer", raising_open)

    tl = TimelapseWriter(config, db)
    tl.source_id = 1
    tl.start()
    try:
        for i in range(5):
            tl.offer(frame(i))
            time.sleep(0.06)
        time.sleep(0.2)
    finally:
        tl.stop()
        tl.join(timeout=5)
    assert not tl.is_alive()
    # Teeth: `_enabled` is only flipped to False inside the except branch
    # added by the fix. Without it, the OSError propagates out of run()
    # uncaught, killing the thread while `_enabled` stays at its initial
    # (config-driven) True value.
    assert tl._enabled is False
    assert db.list_recordings(kind="timelapse") == []


def test_stop_flushes_buffered_samples(
    tmp_path: Path, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Samples that are still queued when stop() fires must be drained and
    written before finalize(), not silently dropped."""
    p = tmp_path / "c2.yaml"
    p.write_text(
        "timelapse:\n  enabled: true\n  dir: "
        + str(tmp_path / "tl2").replace("\\", "/")
        + "\n  sample_interval_seconds: 0.01\n  fps: 30\n",
        encoding="utf-8",
    )
    cfg = load_config(p)

    real_open = timelapse_module.open_video_writer
    opened = threading.Event()

    def slow_open(*args: object, **kwargs: object):
        # Signal that the writer thread has entered the open call, then
        # block long enough for several more samples to pile up in the
        # queue while the writer thread cannot consume them.
        opened.set()
        time.sleep(0.3)
        return real_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(timelapse_module, "open_video_writer", slow_open)

    tl = TimelapseWriter(cfg, db)
    tl.source_id = 1
    tl.start()
    tl.offer(frame(0))
    assert wait_until(lambda: opened.is_set(), timeout=2.0)
    # The background thread is now blocked inside the (slowed) open call;
    # queue a few more samples it has had no chance to consume yet.
    for i in range(1, 4):
        tl.offer(frame(i))
        time.sleep(0.02)
    tl.stop()
    tl.join(timeout=5)
    assert not tl.is_alive()
    recs = db.list_recordings(kind="timelapse")
    assert len(recs) == 1
    path = Path(recs[0].path)
    assert path.exists() and path.stat().st_size > 0
    cap = cv2.VideoCapture(str(path))
    try:
        frames = 0
        while cap.read()[0]:
            frames += 1
        # 1 frame written before the writer-open call blocked, plus the 3
        # queued while it was blocked; only reachable if the post-loop
        # drain (Fix 2) writes the still-queued samples instead of
        # dropping them when the loop exits on stop().
        assert frames >= 3
    finally:
        cap.release()
