"""Robust MJPEG frame extraction by scanning JPEG SOI/EOI markers.

Deliberately ignores multipart boundaries: real-world cameras produce
malformed boundaries, but SOI/EOI scanning survives them.
"""
from __future__ import annotations

_SOI = b"\xff\xd8"
_EOI = b"\xff\xd9"


class MJPEGParser:
    def __init__(self, max_buffer: int = 20_000_000) -> None:
        self._buffer = bytearray()
        self._max_buffer = max_buffer
        self.corrupt_count = 0

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer.extend(chunk)
        frames: list[bytes] = []
        while True:
            soi = self._buffer.find(_SOI)
            if soi < 0:
                # No SOI found; if EOI remnant exists, count as corrupt and clear buffer
                if self._buffer.find(_EOI) >= 0:
                    self.corrupt_count += 1
                self._buffer.clear()
                break
            # Check for orphan EOI before this SOI
            if soi > 0 and self._buffer.find(_EOI, 0, soi) >= 0:
                self.corrupt_count += 1
            eoi = self._buffer.find(_EOI, soi + 2)
            if eoi < 0:
                # Frame not yet complete; discard garbage before SOI
                if soi > 0:
                    del self._buffer[:soi]
                break
            frames.append(bytes(self._buffer[soi:eoi + 2]))
            del self._buffer[:eoi + 2]
        if len(self._buffer) > self._max_buffer:
            self._buffer.clear()
        return frames
