import numpy as np

from server.clipenc import encode_clip


def _frames(n=12, h=120, w=160):
    rng = np.random.default_rng(0)
    return [rng.integers(0, 255, (h, w, 3), dtype=np.uint8) for _ in range(n)]


def test_encodes_a_playable_clip(tmp_path) -> None:
    path = encode_clip(_frames(), tmp_path, "sight_1", fps=10.0)
    assert path is not None
    assert path.exists() and path.stat().st_size > 0
    # must be a browser-playable container — H.264 mp4 or WebM, never the unplayable mp4v .avi
    assert path.suffix in (".mp4", ".webm")


def test_too_few_frames_returns_none(tmp_path) -> None:
    assert encode_clip(_frames(2), tmp_path, "x") is None
    assert encode_clip([], tmp_path, "y") is None


def test_handles_mismatched_frame_sizes(tmp_path) -> None:
    fs = _frames(6, 120, 160)
    fs.append(np.zeros((90, 130, 3), dtype=np.uint8))   # odd one out — resized, not crashed
    path = encode_clip(fs, tmp_path, "mixed", fps=8.0)
    assert path is not None and path.exists()
