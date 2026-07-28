"""FastAPI bridge: WebSocket (events/detections/metrics) + MJPEG proxy + REST."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from core.logging_setup import setup_logging
from .backend import Backend

log = logging.getLogger("overseer.server")

app = FastAPI(title="OVERSEER Bridge")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

backend: Backend | None = None
_clients: set[WebSocket] = set()


async def broadcast(msg: dict[str, Any]) -> None:
    dead = []
    for ws in list(_clients):
        try:
            await ws.send_json(msg)
        except Exception:  # noqa: BLE001
            dead.append(ws)
    for ws in dead:
        _clients.discard(ws)


def _system_payload() -> dict[str, Any]:
    cpu = ram = 0.0
    gpu = None
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
    except Exception:  # noqa: BLE001
        pass
    try:
        import pynvml  # type: ignore
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        gpu = float(pynvml.nvmlDeviceGetUtilizationRates(h).gpu)
    except Exception:  # noqa: BLE001
        gpu = None
    storage = 0.0
    if backend is not None:
        try:
            storage = backend.db.total_recordings_size() / 1e9
        except Exception:  # noqa: BLE001
            pass
    base = {"cpu": cpu, "gpu": gpu, "ram": ram, "storageGB": round(storage, 2),
            "rec": "off", "recActive": False}
    if backend is not None:
        base.update(backend.rec_state())
    return base


async def _heartbeat() -> None:
    while True:
        await asyncio.sleep(2.0)
        await broadcast({"t": "system", "d": _system_payload()})
        if backend is not None:
            await broadcast({"t": "cameras", "d": backend.sources_payload()})
            backend.reap_thumbs()


@app.on_event("startup")
async def _startup() -> None:
    global backend
    setup_logging(Path("logs"), "INFO")
    backend = Backend()
    backend.bind(asyncio.get_running_loop(), broadcast)
    asyncio.create_task(_heartbeat())
    log.info("OVERSEER bridge ready")


@app.on_event("shutdown")
async def _shutdown() -> None:
    if backend is not None:
        backend.shutdown()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _clients.add(ws)
    if backend is not None:
        await ws.send_json({"t": "cameras", "d": backend.sources_payload()})
        await ws.send_json({"t": "conn", "d": backend._conn})
    try:
        while True:
            data = await ws.receive_json()
            _handle_command(str(data.get("d", "")))
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)


def _handle_command(cmd: str) -> None:
    if backend is None or not cmd:
        return
    head, _, arg = cmd.partition(":")
    loop = asyncio.get_running_loop()
    if head == "connect":
        src = _resolve_source(arg.strip())
        if src is not None:  # heavy (opens stream, loads YOLO) -> run off the event loop
            loop.run_in_executor(None, backend.connect, src)
    elif head == "disconnect":
        loop.run_in_executor(None, backend.disconnect)
    elif head == "record":
        backend.record_toggle()
    elif head == "snapshot":
        backend.snapshot()
    elif head == "ooi":  # arg = "name|x,y,w,h" (normalized)
        try:
            name, coords = arg.split("|", 1)
            bbox = [float(v) for v in coords.split(",")]
            backend.ooi_register(name.strip() or "OBJECT", bbox)
        except Exception:  # noqa: BLE001
            pass
    else:
        log.info("command ignored: %s", cmd)


def _resolve_source(token: str) -> int | None:
    if backend is None:
        return None
    if token.isdigit():
        return int(token)
    tl = token.lower()
    for s in backend.db.list_sources():
        if s.name.lower() == tl or tl in s.name.lower():
            return s.id
    return None


@app.get("/stream/{source_id}")
async def stream(source_id: str) -> StreamingResponse:
    async def gen():
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
        while True:
            # the requested camera specifically: analysed frame if it's the active
            # one, else the persistent warm relay — so the feed never gaps on switch.
            jpeg = backend.stream_frame(source_id) if backend else None
            if jpeg:
                yield boundary + jpeg + b"\r\n"
            await asyncio.sleep(1 / 15)
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/thumb/{source_id}")
async def thumb(source_id: int) -> StreamingResponse:
    """Lightweight live preview for the source picker (no analysis)."""
    async def gen():
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
        while True:
            jpeg = backend.thumb_jpeg(source_id) if backend else None
            if jpeg:
                yield boundary + jpeg + b"\r\n"
            await asyncio.sleep(1 / 10)
    return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")


async def _thumb_jpeg(source_id: int) -> bytes | None:
    # off the event loop: thumb_jpeg touches the filesystem / decode workers, and with many
    # cameras polling at once a synchronous call could stall every other request.
    return await asyncio.to_thread(backend.thumb_jpeg, source_id) if backend else None


@app.get("/snap/{source_id}")
async def snap(source_id: int) -> Response:
    """Single latest JPEG for a camera preview (robust polled thumbnail)."""
    jpeg = await _thumb_jpeg(source_id)
    for _ in range(16):  # give the relay worker up to ~2.5s to produce the first frame
        if jpeg:
            break
        await asyncio.sleep(0.15)
        jpeg = await _thumb_jpeg(source_id)
    if not jpeg:
        return Response(status_code=503)
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/sources")
async def api_sources() -> Any:
    return backend.sources_payload() if backend else []


@app.post("/api/sources")
async def api_add_source(payload: dict[str, str]) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    sid = backend.add_source(payload["name"], payload["url"])  # auto-places it on the map
    return {"id": sid}


@app.post("/api/discover")
async def api_discover(payload: dict[str, Any]) -> Any:
    """ONVIF auto-discovery: find cameras on the LAN; with credentials, also resolve their
    rtsp:// URLs so they can be added in one click."""
    from .onvif import discover, stream_uri
    timeout = float(payload.get("timeout", 3.0) or 3.0)
    user = str(payload.get("user", "") or "")
    password = str(payload.get("password", "") or "")
    devices = await asyncio.to_thread(discover, min(max(timeout, 1.0), 8.0))
    if user:
        for d in devices:
            if d.get("xaddr"):
                d["rtsp"] = await asyncio.to_thread(stream_uri, d["xaddr"], user, password)
    return {"devices": devices}


@app.put("/api/sources/{source_id}")
async def api_update_source(source_id: int, payload: dict[str, str]) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    src = next((s for s in backend.db.list_sources() if s.id == source_id), None)
    mx = src.map_x if src else None
    my = src.map_y if src else None
    backend.db.update_source(source_id, payload["name"], payload["url"], mx, my)
    return {"ok": True}


@app.delete("/api/sources/{source_id}")
async def api_delete_source(source_id: int) -> Any:
    if backend is not None:
        backend.db.delete_source(source_id)
    return {"ok": True}


@app.post("/api/shutdown")
async def api_shutdown() -> Any:
    if backend is not None:
        backend.shutdown()
    asyncio.get_event_loop().call_later(0.3, lambda: os._exit(0))
    return {"ok": True}


@app.post("/api/sources/{source_id}/coords")
async def api_set_coords(source_id: int, payload: dict[str, float]) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    lat, lng = float(payload["lat"]), float(payload["lng"])
    backend.db.set_source_position(source_id, lat, lng)  # map_x=lat, map_y=lng
    return {"ok": True, "coords": [lat, lng]}


@app.get("/api/ai/status")
async def api_ai_status() -> Any:
    return backend.ai.status() if backend else {"enabled": False}


@app.post("/api/ai/config")
async def api_ai_config(payload: dict[str, Any]) -> Any:
    """Save provider/base URL/API key/model (catalog 226). OpenAI-standard, so any
    compatible provider works. An empty api_key keeps the stored one."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    return await asyncio.to_thread(backend.ai.save_config, payload)


@app.post("/api/ai/test")
async def api_ai_test(payload: dict[str, Any]) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    return await asyncio.to_thread(backend.ai.test, payload or None)


# Every LLM endpoint below is a graceful no-op when its feature is switched off or the
# provider is unreachable: it returns a normal 200 with an empty/disabled result, never
# a 5xx. The UI treats "disabled" as "hide the affordance", so the app is unaffected.
@app.post("/api/ai/chat")
async def api_ai_chat(payload: dict[str, str]) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    if not backend.ai.feature("chat"):
        return {"reply": None, "disabled": True}
    reply = await asyncio.to_thread(backend.ai.chat, payload.get("prompt", ""), payload.get("system") or None)
    return {"reply": reply}


@app.post("/api/ai/query")
async def api_ai_query(payload: dict[str, str]) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    if not backend.ai.feature("search"):
        return {"filter": None, "disabled": True}
    flt = await asyncio.to_thread(backend.ai.query, payload.get("text", ""))
    return {"filter": flt}


@app.post("/api/ai/summarize")
async def api_ai_summarize(payload: dict[str, Any]) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    if not backend.ai.feature("summarize"):
        return {"summary": None, "disabled": True}
    events = payload.get("events") or []
    text = await asyncio.to_thread(backend.ai.summarize, events)
    return {"summary": text}


@app.post("/api/ai/explain")
async def api_ai_explain(payload: dict[str, Any]) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    if not backend.ai.feature("explain"):
        return {"explanation": None, "disabled": True}
    text = await asyncio.to_thread(backend.ai.explain, payload.get("alert") or {})
    return {"explanation": text}


@app.post("/api/ai/describe/{source_id}")
async def api_ai_describe(source_id: str) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    if not backend.ai.feature("vision"):
        return {"description": None, "disabled": True}
    frame = backend._source_frame_by_id(source_id)
    if frame is None:
        return {"description": None}
    text = await asyncio.to_thread(backend.ai.describe, frame)
    return {"description": text}


@app.post("/api/ai/rule")
async def api_ai_rule(payload: dict[str, Any]) -> Any:
    """Natural language → alert rule (idea 1). With apply=true, creates the rule and
    refreshes the live alert engine; otherwise returns it as a preview."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    if not backend.ai.feature("rules"):
        return {"rule": None, "disabled": True}
    # A pre-parsed rule (from an earlier preview) is created directly — no second LLM call.
    rule = payload.get("rule") if isinstance(payload.get("rule"), dict) else None
    if rule is None:
        text = str(payload.get("text", ""))
        cams = [{"id": s.id, "name": s.name} for s in backend.db.list_sources()]
        zones = [{"id": z.id, "name": z.name} for z in backend.db.list_zones()]
        rule = await asyncio.to_thread(backend.ai.make_rule, text, cams, zones)
    else:
        et = str(rule.get("event_type") or "").upper()
        from .ai_llm import _RULE_EVENTS
        if et not in _RULE_EVENTS:
            rule = None
        else:
            rule["event_type"] = et
            sev = str(rule.get("severity") or "warning").lower()
            rule["severity"] = sev if sev in ("info", "warning", "critical") else "warning"
    if not rule:
        return {"rule": None}
    created = None
    if payload.get("apply"):
        try:
            rid = backend.db.add_alert_rule(
                str(rule.get("name") or rule["event_type"]), rule["event_type"],
                source_id=rule.get("source_id"), zone_id=rule.get("zone_id"),
                min_count=rule.get("min_count"), min_confidence=rule.get("min_confidence"),
                severity=rule["severity"], cooldown_s=float(rule.get("cooldown_s") or 60.0))
            backend.alert_engine.set_rules(backend.db.list_alert_rules())
            created = rid
        except Exception:  # noqa: BLE001
            created = None
    return {"rule": rule, "created": created}


@app.post("/api/ai/correlate")
async def api_ai_correlate(payload: dict[str, Any]) -> Any:
    """Reason over recent alerts — are several one incident? (idea 5)."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    if not backend.ai.feature("correlate"):
        return {"result": None, "disabled": True}
    result = await asyncio.to_thread(backend.ai.correlate, payload.get("alerts") or [])
    return {"result": result}


@app.post("/api/ai/advise")
async def api_ai_advise(payload: dict[str, Any]) -> Any:
    """One recommended operator action for an alert (idea 7)."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    if not backend.ai.feature("advise"):
        return {"action": None, "disabled": True}
    text = await asyncio.to_thread(backend.ai.advise, payload.get("alert") or {})
    return {"action": text}


@app.post("/api/ai/searchevents")
async def api_ai_searchevents(payload: dict[str, Any]) -> Any:
    """Semantic search over the event timeline (idea 10)."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    if not backend.ai.feature("semantic"):
        return {"result": None, "disabled": True}
    result = await asyncio.to_thread(backend.ai.search_events, payload.get("text", ""), payload.get("events") or [])
    return {"result": result}


@app.post("/api/visualmatch")
async def api_visualmatch(payload: dict[str, Any]) -> Any:
    """Find a watchlist entity across cameras by appearance (image processing)."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    import base64
    import cv2
    import numpy as np
    data = str(payload.get("image", ""))
    if "," in data:
        data = data.split(",", 1)[1]
    try:
        img = cv2.imdecode(np.frombuffer(base64.b64decode(data), np.uint8), cv2.IMREAD_COLOR)
    except Exception:  # noqa: BLE001
        img = None
    if img is None:
        return {"matches": []}
    kind = payload.get("kind") or None
    # Operator feedback can raise the bar per class (self-tuning, catalog 13).
    try:
        thresh = float(payload.get("minScore") or 0.42)
    except (TypeError, ValueError):
        thresh = 0.42
    thresh = min(max(thresh, 0.3), 0.85)
    matches = await asyncio.to_thread(backend.visual_match, img, kind, thresh)
    return {"matches": matches}


@app.post("/api/platematch")
async def api_platematch(payload: dict[str, Any]) -> Any:
    """Find a vehicle across live cameras by licence plate (ANPR)."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    plate = str(payload.get("plate", "")).strip()
    if not plate:
        return {"matches": []}
    matches = await asyncio.to_thread(backend.plate_match, plate)
    return {"matches": matches}


@app.get("/api/roster")
async def api_roster() -> Any:
    """Session roster: every person/vehicle seen, deduped, with a photo (+plate for cars)."""
    return backend.roster.list() if backend else []


@app.get("/api/roster/{det_id}")
async def api_roster_entry(det_id: str) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    entry = backend.roster.get(det_id)
    return entry if entry else JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/roster/{det_id}/watch")
async def api_roster_watch(det_id: str, body: dict) -> Any:
    """Flag/unflag a subject as watched (BOLO). While watched, re-identifying the subject on
    any camera raises a WATCHLIST HIT alert."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    entry = backend.roster.watch(det_id, bool(body.get("on", True)))
    return entry if entry else JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/roster/{det_id}/cutout")
async def api_roster_cutout(det_id: str) -> Response:
    """The roster photo with its background removed (YOLO-seg), as a transparent PNG."""
    if backend is None:
        return Response(status_code=503)
    png = await asyncio.to_thread(backend.roster.cutout_png, det_id)
    if not png:
        return Response(status_code=404)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/inspect/{source_id}")
async def api_inspect(source_id: int, payload: dict[str, float]) -> Any:
    """'Look closer' at a clicked point — returns any objects found there."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    dets = await asyncio.to_thread(backend.inspect, float(payload.get("x", 0.5)), float(payload.get("y", 0.5)))
    return {"detections": dets}


@app.post("/api/ptz/{source_id}")
async def api_ptz(source_id: int, payload: dict[str, float]) -> Any:
    """Best-effort PTZ ContinuousMove (feature 13). pan/tilt/zoom in [-1,1]."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    from urllib.parse import urlparse
    src = next((s for s in backend.db.list_sources() if s.id == source_id), None)
    if src is None:
        return JSONResponse({"error": "no source"}, status_code=404)
    u = urlparse(src.url if "://" in src.url else f"//{src.url}")
    res = await asyncio.to_thread(
        backend.ptz.move, u.hostname or src.url,
        float(payload.get("pan", 0)), float(payload.get("tilt", 0)), float(payload.get("zoom", 0)),
        port=u.port or 80, user=u.username or "admin", pwd=u.password or "",
    )
    return res


@app.post("/api/connect/{source_id}")
async def api_connect(source_id: int) -> Any:
    if backend:
        await asyncio.to_thread(backend.connect, source_id)
    return {"ok": True}


@app.post("/api/disconnect")
async def api_disconnect() -> Any:
    if backend:
        await asyncio.to_thread(backend.disconnect)
    return {"ok": True}


def _snap_url(p: Any) -> str | None:
    if not p:
        return None
    s = str(p).replace("\\", "/")
    return "/snapshots/" + s.split("/snapshots/")[-1] if "/snapshots/" in s else None


@app.get("/api/events")
async def api_events(limit: int = 200) -> Any:
    if backend is None:
        return []
    return [
        {"id": e.id, "ts": e.timestamp * 1000, "type": e.type, "label": e.label,
         "conf": e.confidence, "snapshot": _snap_url(e.snapshot_path)}
        for e in backend.db.list_events(limit=limit)
    ]


@app.get("/api/alerts")
async def api_alerts(limit: int = 200) -> Any:
    if backend is None:
        return []
    return [
        {"ts": a.timestamp * 1000, "severity": a.severity, "type": a.event_type,
         "summary": a.summary, "ack": a.acknowledged}
        for a in backend.db.list_alerts(limit=limit)
    ]


@app.get("/api/stats")
async def api_stats(start: float, end: float) -> Any:
    if backend is None:
        return {}
    return backend.db.event_type_counts(start, end)


@app.get("/api/search")
async def api_search(q: str = "", source: int | None = None,
                     start: float | None = None, end: float | None = None) -> Any:
    if backend is None:
        return {"hits": []}
    try:
        from forensic.search import ForensicSearchService
        filters: dict[str, Any] = {}
        if source is not None:
            filters["source_id"] = source
        if start is not None:
            filters["start"] = start
        if end is not None:
            filters["end"] = end
        res = ForensicSearchService(backend.db).search(q or None, filters or None)
        return {"hits": [
            {"kind": h.kind, "ts": h.ts * 1000, "type": h.type, "label": h.label,
             "snapshot": h.snapshot_path} for h in res.hits
        ], "deferred": res.deferred_terms, "unmatched": res.unmatched}
    except Exception as exc:  # noqa: BLE001
        return {"hits": [], "error": str(exc)}


@app.get("/api/recordings")
async def api_recordings(limit: int = 60) -> Any:
    if backend is None:
        return []
    out = []
    for r in backend.db.list_recordings(limit=limit):
        out.append({"id": r.id, "kind": r.kind, "mode": r.mode,
                    "start": r.start_ts * 1000, "end": r.end_ts * 1000,
                    "sizeMB": round((r.size_bytes or 0) / 1e6, 1),
                    "url": f"/rec/{r.id}" if Path(r.path).suffix.lower() in (".mp4", ".webm", ".avi") else None})
    return out


@app.delete("/api/recordings/{rec_id}")
async def api_delete_recording(rec_id: int) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    rec = next((r for r in backend.db.list_recordings(limit=9999) if r.id == rec_id), None)
    if rec is not None:
        try:
            Path(rec.path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        backend.db.delete_recording(rec_id)
    return {"ok": True}


@app.post("/api/storage/cleanup")
async def api_storage_cleanup(payload: dict[str, str]) -> Any:
    """Delete safe-to-remove artefacts: alert snapshots+clips, or all recordings.
    None of these are required for the system to keep running."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    what = payload.get("what", "")
    freed = 0

    def _rm_dir(d: Path) -> int:
        n = 0
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file():
                    try:
                        f.unlink(); n += 1
                    except Exception:  # noqa: BLE001
                        pass
        return n

    snap_dir = backend.data_dir / "snapshots"
    if what in ("snapshots", "all"):
        freed += _rm_dir(snap_dir)  # alert images + clips subdir
    elif what == "clips":
        freed += _rm_dir(snap_dir / "clips")
    elif what == "recordings":
        for r in backend.db.list_recordings(limit=9999):
            try:
                Path(r.path).unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                pass
            backend.db.delete_recording(r.id); freed += 1
    return {"ok": True, "removed": freed}


@app.get("/rec/{rec_id}")
async def rec(rec_id: int) -> Any:
    if backend is None:
        return Response(status_code=404)
    r = next((x for x in backend.db.list_recordings(limit=500) if x.id == rec_id), None)
    if r is None or not Path(r.path).exists():
        return Response(status_code=404)
    media = "video/mp4" if Path(r.path).suffix.lower() == ".mp4" else "application/octet-stream"
    return FileResponse(r.path, media_type=media)


@app.get("/api/storage")
async def api_storage() -> Any:
    if backend is None:
        return {}
    db = backend.db
    try:
        recent = [
            {"kind": r.kind, "start": r.start_ts * 1000, "end": r.end_ts * 1000,
             "sizeMB": round((r.size_bytes or 0) / 1e6, 1), "mode": r.mode}
            for r in db.list_recordings(limit=30)
        ]
    except Exception:  # noqa: BLE001
        recent = []
    return {
        "recordings": db.count_recordings(),
        "sizeGB": round((db.total_recordings_size() or 0) / 1e9, 2),
        "oldest": (db.oldest_recording_ts() or 0) * 1000,
        "recent": recent,
    }


@app.get("/api/cases")
async def api_cases() -> Any:
    if backend is None:
        return []
    return [
        {"id": c.id, "name": c.name, "threat": c.threat_level, "notes": c.notes,
         "created": c.created_at * 1000, "targets": len(backend.db.list_case_targets(c.id))}
        for c in backend.db.list_cases()
    ]


@app.post("/api/cases")
async def api_add_case(payload: dict[str, str]) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    cid = backend.db.add_case(
        payload["name"], threat_level=payload.get("threat", "low"), notes=payload.get("notes", ""),
    )
    return {"id": cid}


# Serve alert/snapshot images (must be mounted before the "/" catch-all).
from core.config import load_config as _load_config  # noqa: E402
_SNAP = Path(str(_load_config(Path("config/default.yaml")).get("app.data_dir", "data"))) / "snapshots"
_SNAP.mkdir(parents=True, exist_ok=True)
app.mount("/snapshots", StaticFiles(directory=str(_SNAP)), name="snaps")

# Serve the built front-end last (so /api and /ws win). Optional.
_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="web")
