"""Encode frames to a short, browser-playable clip.

Prefers H.264 MP4, but many OpenCV builds (notably Windows without a bundled OpenH264) can't
open the avc1 encoder; it then falls back to WebM (VP9 → VP8), which the in-app <video> plays.
It must NEVER produce mp4v — that is MPEG-4 Part 2, which the browser writes to disk but cannot
play, and is the reason a clip can "record but not play".
"""
from __future__ import annotations

from pathlib import Path

import cv2

# (fourcc, extension), best browser compatibility first. mp4v is deliberately absent.
_ATTEMPTS = (("avc1", "mp4"), ("vp09", "webm"), ("VP80", "webm"))


def encode_clip(frames: list, out_dir: str | Path, stem: str, fps: float = 10.0) -> Path | None:
    """Write frames to out_dir/<stem>.<ext> in the first codec this build can actually open,
    returning the written Path, or None if nothing worked / too few frames."""
    if not frames or len(frames) < 3:
        return None
    h, w = frames[0].shape[:2]
    out = Path(out_dir)
    try:
        out.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    for fourcc, ext in _ATTEMPTS:
        path = out / f"{stem}.{ext}"
        try:
            vw = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), float(fps), (w, h))
            if not vw.isOpened():          # this build lacks the encoder — try the next
                vw.release()
                path.unlink(missing_ok=True)
                continue
            for f in frames:
                vw.write(f if f.shape[:2] == (h, w) else cv2.resize(f, (w, h)))
            vw.release()
        except Exception:  # noqa: BLE001
            path.unlink(missing_ok=True)
            continue
        if path.exists() and path.stat().st_size > 0:
            return path
        path.unlink(missing_ok=True)
    return None
