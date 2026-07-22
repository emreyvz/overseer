import time
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np
import pytest

from camera.frame_buffer import Frame
from core.config import Config, load_config
from storage.database import Database
from storage.recorder import Recorder


@pytest.fixture()
def config(tmp_path: Path) -> Config:
    p = tmp_path / "c.yaml"
    p.write_text(
        "recording:\n  mode: event\n  dir: " + str(tmp_path / "rec").replace("\\", "/")
        + "\n  fps: 15\n  pre_roll_seconds: 0.3\n  post_roll_seconds: 0.4\n"
        "  segment_seconds: 2\n  max_buffer_mb: 64\n  codecs: [mp4v, XVID, MJPG]\n",
        encoding="utf-8",
    )
    return load_config(p)


@pytest.fixture()
def continuous_config(tmp_path: Path) -> Config:
    p = tmp_path / "c_cont.yaml"
    p.write_text(
        "recording:\n  mode: continuous\n  dir: " + str(tmp_path / "rec_cont").replace("\\", "/")
        + "\n  fps: 15\n  pre_roll_seconds: 0.3\n  post_roll_seconds: 0.4\n"
        # Long segment so rotation never fires during these tests.
        "  segment_seconds: 30\n  max_buffer_mb: 64\n  codecs: [mp4v, XVID, MJPG]\n",
        encoding="utf-8",
    )
    return load_config(p)


@pytest.fixture()
def db(tmp_path: Path) -> Iterator[Database]:
    d = Database(tmp_path / "r.db")
    yield d
    d.close()


def frame(i: int) -> Frame:
    img = np.full((48, 64, 3), (i * 13) % 255, dtype=np.uint8)
    return Frame(image=img, timestamp=time.time(), seq=i)


def wait_until(cond, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_event_mode_records_clip(config: Config, db: Database) -> None:
    rec = Recorder(config, db)
    rec.source_id = 1
    rec.start()
    try:
        for i in range(6):
            rec.offer(frame(i), motion_active=False)
            time.sleep(0.02)
        rec.trigger("PERSON")
        for i in range(6, 40):
            rec.offer(frame(i), motion_active=False)
            time.sleep(0.02)
        assert wait_until(lambda: len(db.list_recordings()) >= 1)
    finally:
        rec.stop()
        rec.join(timeout=5)
    recs = db.list_recordings()
    assert recs[0].kind == "clip"
    assert recs[0].mode == "event"
    assert recs[0].trigger == "PERSON"
    path = Path(recs[0].path)
    assert path.exists() and path.stat().st_size > 0
    cap = cv2.VideoCapture(str(path))
    try:
        frames = 0
        while cap.read()[0]:
            frames += 1
        assert frames >= 1
    finally:
        cap.release()


def test_off_mode_records_nothing(config: Config, db: Database) -> None:
    config.set("recording.mode", "off")
    rec = Recorder(config, db)
    rec.start()
    try:
        for i in range(20):
            rec.offer(frame(i), motion_active=True)
            time.sleep(0.01)
        time.sleep(0.3)
    finally:
        rec.stop()
        rec.join(timeout=5)
    assert db.list_recordings() == []


def test_set_mode_and_current_mode(config: Config, db: Database) -> None:
    rec = Recorder(config, db)
    assert rec.current_mode() == "event"
    rec.set_mode("off")
    assert rec.current_mode() == "off"
    rec.set_mode("motion")
    assert rec.current_mode() == "motion"


def test_stop_without_start_is_safe(config: Config, db: Database) -> None:
    rec = Recorder(config, db)
    rec.stop()  # never started; must not raise


class CountingRecorder(Recorder):
    """Recorder subclass that counts every call to _write for test assertions."""

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.writes = 0

    def _write(self, image) -> None:
        self.writes += 1
        super()._write(image)


def test_set_mode_off_closes_open_clip(continuous_config: Config, db: Database) -> None:
    rec = Recorder(continuous_config, db)
    rec.source_id = 1
    rec.start()
    try:
        for i in range(15):
            rec.offer(frame(i), motion_active=False)
            time.sleep(0.02)
        assert wait_until(rec.is_recording)
        rec.set_mode("off")
        for i in range(15, 17):
            rec.offer(frame(i), motion_active=False)
            time.sleep(0.02)
        # The open continuous clip must be closed once the mode no longer
        # matches, instead of growing unbounded until stop().
        assert wait_until(lambda: not rec.is_recording())
    finally:
        rec.stop()
        rec.join(timeout=5)
    recs = db.list_recordings()
    assert len(recs) >= 1
    assert recs[0].kind == "clip"
    # Regression guard: the clip was recorded under "continuous" mode, and
    # the mode only flipped to "off" as the close reason. The persisted
    # mode must reflect what the clip was actually recorded under, not the
    # live mode at close time.
    assert recs[0].mode == "continuous"
    path = Path(recs[0].path)
    assert path.exists() and path.stat().st_size > 0


def test_continuous_writes_each_frame_once(continuous_config: Config, db: Database) -> None:
    rec = CountingRecorder(continuous_config, db)
    rec.source_id = 1
    rec.start()
    try:
        n = 20
        for i in range(n):
            rec.offer(frame(i), motion_active=False)
            time.sleep(0.02)
        # Pace offers slowly enough (queue cap is 2*fps=30 >= n) that none
        # drop, then wait for the recorder thread to fully drain the queue.
        assert wait_until(lambda: rec.writes >= n)
    finally:
        rec.stop()
        rec.join(timeout=5)
    # No rotation happens (segment_seconds is large) and no pre-roll flush
    # happens in continuous mode, so every offered frame is written exactly
    # once: no double-write, no pre-roll duplication.
    assert rec.writes == n


def test_open_clip_twice_in_same_second_gets_distinct_paths(
    continuous_config: Config, db: Database
) -> None:
    rec = Recorder(continuous_config, db)
    rec.source_id = 1
    img = frame(0).image
    now = time.time()
    rec._open_clip(img, "continuous", now)
    first_path = rec._writer_path
    rec._close_clip(now)
    rec._open_clip(img, "continuous", now)  # same `now` -> identical stamp
    second_path = rec._writer_path
    rec._close_clip(now)
    assert first_path is not None and second_path is not None
    assert first_path != second_path


def test_request_close_closes_clip_and_clears_buffers(
    continuous_config: Config, db: Database
) -> None:
    rec = Recorder(continuous_config, db)
    rec.source_id = 1
    rec.start()
    try:
        for i in range(15):
            rec.offer(frame(i), motion_active=False)
            time.sleep(0.02)
        assert wait_until(rec.is_recording)
        rec.request_close()
        # request_close() must close the open clip promptly, without
        # requiring a mode change or waiting for segment rotation.
        assert wait_until(lambda: not rec.is_recording())
        with rec._lock:
            assert len(rec._queue) == 0
            assert len(rec._ring) == 0
            assert rec._ring_bytes == 0
    finally:
        rec.stop()
        rec.join(timeout=5)
    recs = db.list_recordings()
    assert len(recs) >= 1
    assert recs[0].kind == "clip"
    path = Path(recs[0].path)
    assert path.exists() and path.stat().st_size > 0


def test_event_preroll_flushed_once(config: Config, db: Database) -> None:
    rec = CountingRecorder(config, db)
    rec.source_id = 1
    rec.start()
    try:
        pretrigger = 6
        for i in range(pretrigger):
            rec.offer(frame(i), motion_active=False)
            time.sleep(0.03)
        rec.trigger("PERSON")
        live = 10
        for i in range(pretrigger, pretrigger + live):
            rec.offer(frame(i), motion_active=False)
            time.sleep(0.03)
        assert wait_until(lambda: rec.writes >= live)
    finally:
        rec.stop()
        rec.join(timeout=5)
    pre_roll_seconds = float(config.get("recording.pre_roll_seconds", 0.3))
    fps = float(config.get("recording.fps", 15.0))
    max_pre_roll_frames = max(1, int(pre_roll_seconds * fps))
    # More writes than the live-only frames proves the pre-roll ring was
    # flushed; no more than live + the ring cap proves it wasn't duplicated.
    assert rec.writes > live
    assert rec.writes <= live + max_pre_roll_frames
    assert rec.writes <= pretrigger + live
