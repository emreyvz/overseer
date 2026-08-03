"""Read-only local REST API: events / alerts / stats as JSON, stdlib only.

Opt-in (``rest_api.enabled``), binds localhost by default. Mirrors the
ThreadingHTTPServer + BaseHTTPRequestHandler pattern used by the test suite's
MJPEG fixture (see tests/conftest.py).
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from queue import Empty, Queue
from urllib.parse import parse_qs, urlparse

from loguru import logger

from core.config import Config
from storage.database import Database
from storage.statistics import day_floor

_MAX_LIMIT = 500


def _make_handler(db: Database) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send_json(self, obj: object, status: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream(self) -> None:
            broadcaster = getattr(self.server, "broadcaster", None)
            if broadcaster is None:
                self._send_json({"error": "unavailable"}, status=503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            q = broadcaster._register()
            try:
                while not broadcaster._closing:
                    try:
                        # Bounded get: wakes periodically to re-check _closing
                        # and to send a keepalive comment on idle connections.
                        payload = q.get(timeout=10)
                    except Empty:
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
                        continue
                    if payload is None:  # stop sentinel
                        break
                    self.wfile.write(
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
                    )
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionError, OSError):
                pass  # client disconnected
            finally:
                broadcaster._unregister(q)

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            try:
                parsed = urlparse(self.path)
                if parsed.path == "/api/stream":
                    self._stream()
                    return
                q = parse_qs(parsed.query)
                if parsed.path == "/api/health":
                    self._handle_health()
                elif parsed.path == "/api/events":
                    self._handle_events(q)
                elif parsed.path == "/api/alerts":
                    self._handle_alerts(q)
                elif parsed.path == "/api/stats":
                    self._handle_stats(q)
                elif parsed.path == "/metrics":
                    self._handle_metrics()
                else:
                    self._send_json({"error": "not found"}, status=404)
            except Exception as exc:  # noqa: BLE001 - never let a handler crash the server
                logger.exception("REST API request failed")
                self._send_json({"error": str(exc)}, status=500)

        def _send_text(self, body: str, status: int = 200) -> None:
            data = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _handle_metrics(self) -> None:
            now = time.time()
            today = day_floor(now)
            lines = [
                "# HELP overseer_events_total Total events recorded.",
                "# TYPE overseer_events_total gauge",
                f"overseer_events_total {db.count_events_since(0.0)}",
                "# HELP overseer_events_today Events recorded since local midnight.",
                "# TYPE overseer_events_today gauge",
                f"overseer_events_today {db.count_events_since(today)}",
                "# HELP overseer_alerts_unacked Unacknowledged alerts.",
                "# TYPE overseer_alerts_unacked gauge",
                f"overseer_alerts_unacked {len(db.list_alerts(limit=100000, unacked_only=True))}",
                "# HELP overseer_event_type_total Events by type since local midnight.",
                "# TYPE overseer_event_type_total gauge",
            ]
            # Upper bound is exclusive (ts < end), and on Windows time.time() can be coarse (~15 ms),
            # so an event created in the same tick as the scrape would be dropped. Extend the end a
            # second past now so a just-recorded event is always counted (a scrape "as of now").
            for etype, count in sorted(db.event_type_counts(today, now + 1.0).items()):
                lines.append(f'overseer_event_type_total{{type="{etype}"}} {count}')
            self._send_text("\n".join(lines) + "\n")

        def _handle_health(self) -> None:
            today_start = day_floor(time.time())
            self._send_json({
                "status": "ok",
                "time": time.time(),
                "events_today": db.count_events_since(today_start),
            })

        def _handle_events(self, q: dict[str, list[str]]) -> None:
            limit = min(int(q.get("limit", ["100"])[0]), _MAX_LIMIT)
            since = float(q["since"][0]) if "since" in q else None
            events = db.list_events(limit=limit, since=since)
            self._send_json([
                {
                    "id": e.id, "timestamp": e.timestamp, "type": e.type,
                    "source_id": e.source_id, "label": e.label,
                    "confidence": e.confidence, "metadata": e.metadata,
                }
                for e in events
            ])

        def _handle_alerts(self, q: dict[str, list[str]]) -> None:
            limit = min(int(q.get("limit", ["100"])[0]), _MAX_LIMIT)
            unacked = q.get("unacked", ["0"])[0] == "1"
            alerts = db.list_alerts(limit=limit, unacked_only=unacked)
            self._send_json([
                {
                    "id": a.id, "timestamp": a.timestamp, "rule_name": a.rule_name,
                    "event_type": a.event_type, "source_id": a.source_id,
                    "severity": a.severity, "summary": a.summary,
                    "acknowledged": a.acknowledged,
                }
                for a in alerts
            ])

        def _handle_stats(self, q: dict[str, list[str]]) -> None:
            now = time.time()
            start = float(q.get("start", [str(now - 86400)])[0])
            end = float(q.get("end", [str(now)])[0])
            self._send_json({"type_counts": db.event_type_counts(start, end)})

        def _reject_write(self) -> None:
            self._send_json({"error": "read-only"}, status=405)

        def do_POST(self) -> None:  # noqa: N802
            self._reject_write()

        def do_PUT(self) -> None:  # noqa: N802
            self._reject_write()

        def do_DELETE(self) -> None:  # noqa: N802
            self._reject_write()

        def do_PATCH(self) -> None:  # noqa: N802
            self._reject_write()

        def log_message(self, *args: object) -> None:
            pass  # keep server logs quiet; app already logs via loguru

    return Handler


class RestApiServer:
    """A minimal, read-only, opt-in REST API over the events/alerts/stats DB."""

    def __init__(self, config: Config, db: Database) -> None:
        self._db = db
        self._enabled = bool(config.get("rest_api.enabled", False))
        self._host = str(config.get("rest_api.host", "127.0.0.1"))
        self._port = int(config.get("rest_api.port", 8787))
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._subscribers: set = set()
        self._sub_lock = threading.Lock()
        self._closing = False

    def broadcast(self, payload: dict) -> None:
        """Fan out an event payload to every connected SSE subscriber.

        Never blocks: a full subscriber queue silently drops the payload
        instead of stalling the caller (typically the event-bus thread).
        """
        with self._sub_lock:
            for q in self._subscribers:
                try:
                    q.put_nowait(payload)
                except Exception:  # noqa: BLE001 - drop when full/closed
                    pass

    def _register(self) -> Queue:
        q: Queue = Queue(maxsize=200)
        with self._sub_lock:
            self._subscribers.add(q)
        return q

    def _unregister(self, q: Queue) -> None:
        with self._sub_lock:
            self._subscribers.discard(q)

    def start(self) -> None:
        if not self._enabled:
            return
        try:
            handler = _make_handler(self._db)
            self._server = ThreadingHTTPServer((self._host, self._port), handler)
            self._server.broadcaster = self
            self._closing = False
            self._thread = threading.Thread(
                target=self._server.serve_forever, daemon=True, name="RestApiServer"
            )
            self._thread.start()
            logger.info("REST API listening on {}", self._server.server_address)
        except Exception:
            logger.exception("REST API failed to start")
            self._server = None
            self._thread = None

    def port_in_use(self) -> int:
        if self._server is not None:
            return int(self._server.server_address[1])
        return self._port

    def stop(self) -> None:
        if self._server is None:
            return
        self._closing = True
        with self._sub_lock:
            for q in self._subscribers:
                try:
                    q.put_nowait(None)  # wake any blocked SSE handler thread
                except Exception:  # noqa: BLE001
                    pass
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            logger.exception("REST API failed to stop cleanly")
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None
