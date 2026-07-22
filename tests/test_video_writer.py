from pathlib import Path

import cv2
import numpy as np

from storage.video_writer import CODEC_EXT, open_video_writer


def test_opens_writer_and_produces_playable_video(tmp_path: Path) -> None:
    result = open_video_writer(tmp_path / "clip", fps=15.0, size=(64, 48),
                               codecs=["mp4v", "XVID", "MJPG"])
    assert result is not None
    writer, path = result
    assert writer.isOpened()
    assert path.suffix in {".mp4", ".avi"}
    for i in range(10):
        frame = np.full((48, 64, 3), i * 20, dtype=np.uint8)
        writer.write(frame)
    writer.release()
    assert path.exists() and path.stat().st_size > 0
    cap = cv2.VideoCapture(str(path))
    try:
        assert cap.isOpened()
        count = 0
        while True:
            ok, _ = cap.read()
            if not ok:
                break
            count += 1
        assert count >= 1  # at least some frames decodable
    finally:
        cap.release()


def test_returns_none_when_no_codec_opens(tmp_path: Path) -> None:
    result = open_video_writer(tmp_path / "clip", fps=15.0, size=(64, 48),
                               codecs=["ZZZZ"])  # invalid fourcc
    assert result is None


def test_extension_matches_codec(tmp_path: Path) -> None:
    result = open_video_writer(tmp_path / "c", fps=15.0, size=(32, 32),
                               codecs=["XVID"])
    assert result is not None
    _, path = result
    assert path.suffix == CODEC_EXT["XVID"]
    result[0].release()
