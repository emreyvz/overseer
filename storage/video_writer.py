"""Codec-fallback helper for cv2.VideoWriter (Windows codec availability varies)."""
from __future__ import annotations

from pathlib import Path

import cv2
from loguru import logger

CODEC_EXT: dict[str, str] = {"mp4v": ".mp4", "XVID": ".avi", "MJPG": ".avi"}


def open_video_writer(
    path: Path, fps: float, size: tuple[int, int], codecs: list[str]
) -> tuple[cv2.VideoWriter, Path] | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for codec in codecs:
        extension = CODEC_EXT.get(codec, ".avi")
        target = path.with_suffix(extension)
        fourcc = cv2.VideoWriter_fourcc(*codec)
        writer = cv2.VideoWriter(str(target), fourcc, fps, size)
        if writer.isOpened():
            logger.info("video writer opened: {} ({})", target.name, codec)
            return writer, target
        writer.release()
        logger.warning("codec {} unavailable, trying next", codec)
    logger.error("no codec could open a video writer for {}", path.name)
    return None
