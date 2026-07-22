"""Shared test fixtures: a local MJPEG server so tests never touch the network."""
from __future__ import annotations

# Stabilize native OpenMP/MKL runtimes BEFORE cv2/numpy/torch load them. The
# full suite mixes OpenCV, NumPy and torch (via forensic/embedding); on
# Windows their duplicate/multi-threaded OpenMP runtimes intermittently
# corrupt the process heap, surfacing as a native segfault in an unrelated
# torch test once enough tests have accumulated. Allowing the duplicate
# runtime and pinning OpenMP to a single thread removes the crash. conftest is
# imported before any test module, so this runs before the native libs load.
import os

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import threading  # noqa: E402
import time  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
from typing import Callable, Iterator  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402


def _make_jpeg() -> bytes:
    img = np.random.default_rng(42).integers(0, 255, (120, 160, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", img)
    assert ok
    return encoded.tobytes()


class _MJPEGHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        jpeg: bytes = self.server.jpeg_bytes  # type: ignore[attr-defined]
        limit: int = self.server.frames_per_connection  # type: ignore[attr-defined]
        interval: float = self.server.frame_interval  # type: ignore[attr-defined]
        for _ in range(limit):
            try:
                self.wfile.write(
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"
                )
                time.sleep(interval)
            except (ConnectionError, OSError):
                return

    def log_message(self, *args: object) -> None:
        pass  # keep test output clean


@pytest.fixture()
def mjpeg_server() -> Iterator[Callable[..., str]]:
    servers: list[ThreadingHTTPServer] = []

    def make_mjpeg_server(frames_per_connection: int = 10_000,
                          frame_interval: float = 0.01) -> str:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _MJPEGHandler)
        server.jpeg_bytes = _make_jpeg()  # type: ignore[attr-defined]
        server.frames_per_connection = frames_per_connection  # type: ignore[attr-defined]
        server.frame_interval = frame_interval  # type: ignore[attr-defined]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_port}/stream.mjpg"

    yield make_mjpeg_server
    for server in servers:
        server.shutdown()
        server.server_close()
