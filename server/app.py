"""FastAPI bridge: WebSocket (events/detections/metrics) + MJPEG proxy + REST."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
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


_nvml_handle: Any = None
_nvml_tried = False


def _gpu_util() -> float | None:
    """GPU utilisation via NVML, initialised ONCE (nvmlInit was being called every heartbeat, a
    periodic hitch on the event loop)."""
    global _nvml_handle, _nvml_tried
    if not _nvml_tried:
        _nvml_tried = True
        try:
            import pynvml  # type: ignore
            pynvml.nvmlInit()
            _nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
        except Exception:  # noqa: BLE001
            _nvml_handle = None
    if _nvml_handle is None:
        return None
    try:
        import pynvml  # type: ignore
        return float(pynvml.nvmlDeviceGetUtilizationRates(_nvml_handle).gpu)
    except Exception:  # noqa: BLE001
        return None


def _system_payload() -> dict[str, Any]:
    cpu = ram = 0.0
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory().percent
    except Exception:  # noqa: BLE001
        pass
    gpu = _gpu_util()
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
        # Build the payloads OFF the event loop (NVML + psutil + SQLite), so the heartbeat never
        # hitches the loop that is serving /stream.
        sysd = await asyncio.to_thread(_system_payload)
        await broadcast({"t": "system", "d": sysd})
        if backend is not None:
            cams = await asyncio.to_thread(backend.sources_payload)
            await broadcast({"t": "cameras", "d": cams})
            backend.reap_thumbs()


def _tune_thread_pools() -> None:
    """Leave the 30fps display / capture / stream threads room to run. OpenCV and torch otherwise
    each fan a single op (resize / imencode / NMS / optical flow) across EVERY core, which in this
    one-process app starves the display-encoder thread in bursts and makes the live feed play frame
    by frame. Cap both to cores-2 so at least two cores are always free for the display path."""
    import os
    n = max(1, (os.cpu_count() or 4) - 2)
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(var, str(n))
    try:
        import cv2
        cv2.setNumThreads(n)
    except Exception:  # noqa: BLE001
        pass
    try:
        import torch
        torch.set_num_threads(n)
    except Exception:  # noqa: BLE001
        pass
    log.info("CPU thread pools capped to %d (cores-2) so the display path is not starved", n)


@app.on_event("startup")
async def _startup() -> None:
    global backend
    setup_logging(Path("logs"), "INFO")
    _tune_thread_pools()
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
    # A SYNC generator: Starlette iterates it in a threadpool, so its pacing (time.sleep) and
    # frame-fetch run OFF the single asyncio event loop. Only the socket send touches the loop, so
    # the video stream stops competing with WebSocket emits / other endpoints for the loop's time.
    def gen():
        import time as _t
        boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
        last = None
        while True:
            jpeg = backend.stream_frame(source_id) if backend else None
            if jpeg is not None and jpeg is not last:   # poll fast, but only send genuinely new frames
                yield boundary + jpeg + b"\r\n"
                last = jpeg
            _t.sleep(1 / 60)   # poll faster than the 30 fps source so no new frame waits
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


@app.post("/api/stt")
async def api_stt(request: Request) -> Any:
    """Offline speech-to-text for the Operator's voice input: the browser records the mic and POSTs
    the audio bytes here; faster-whisper transcribes on-device. lang query param 'en'/'tr' or auto."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    from server import stt
    lang = request.query_params.get("lang") or None
    audio = await request.body()
    text = await asyncio.to_thread(stt.transcribe, audio, lang)
    return {"text": text, "disabled": text is None and not stt.available()}


@app.post("/api/enhance/{source_id}")
async def api_enhance(source_id: str, payload: dict[str, Any]) -> Any:
    """Live 'enhance': clarify a boxed region of a camera's current frame into a photographic
    close-up. box is [x, y, w, h] normalized. Returns {image: data-url | null}."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    box = payload.get("box")
    if not isinstance(box, list) or len(box) != 4:
        return {"image": None}
    frame = backend._source_frame_by_id(source_id)
    if frame is None:
        return {"image": None}
    from server import enhance
    img = await asyncio.to_thread(enhance.enhance_region, frame, box, backend._sr)
    return {"image": img}


@app.get("/api/tts")
async def api_tts(text: str = "", lang: str = "") -> Any:
    """Offline text-to-speech for the Operator's spoken replies: returns WAV audio the browser plays.
    A reliable alternative to the browser voice (which often has no voices in Electron)."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    from server import tts
    data = await asyncio.to_thread(tts.synth, text[:400], lang or None)
    if not data:
        return JSONResponse({"disabled": True})   # frontend falls back to the browser voice
    return Response(content=data, media_type="audio/wav")


@app.post("/api/ai/operate")
async def api_ai_operate(payload: dict[str, Any]) -> Any:
    """AI Operator: plan a natural-language command into a chain of UI actions. The frontend
    router handles the common commands locally with zero latency and only falls back here for
    complex / multi-step / referential ones."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    if not backend.ai.feature("operate"):
        return {"steps": [], "disabled": True}
    plan = await asyncio.to_thread(
        backend.ai.plan_command, str(payload.get("command", "")), payload.get("context") or {})
    return plan or {"steps": [], "say": "I could not turn that into an action."}


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


@app.post("/api/ai/vqa/{source_id}")
async def api_ai_vqa(source_id: str, payload: dict[str, str]) -> Any:
    """Vision Q&A: answer a free-form question about a camera's current frame using the VLM."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    if not backend.ai.feature("vision"):
        return {"answer": None, "disabled": True}
    frame = backend._source_frame_by_id(source_id)
    if frame is None:
        return {"answer": None}
    text = await asyncio.to_thread(backend.ai.vqa, frame, str(payload.get("question", "")))
    return {"answer": text}


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


# NOTE: these static-path routes must precede /api/roster/{det_id} so they aren't swallowed.
@app.get("/api/roster/merge-candidates")
async def api_merge_candidates() -> Any:
    """Likely-duplicate identities to review in the merge center."""
    if backend is None:
        return {"candidates": []}
    cands = await asyncio.to_thread(backend.roster.merge_candidates)
    return {"candidates": cands}


@app.post("/api/roster/merge")
async def api_roster_merge(payload: dict) -> Any:
    """Fold two roster entries into one canonical identity."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    merged = backend.roster.merge(str(payload.get("keep", "")), str(payload.get("drop", "")))
    return merged if merged else JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/roster/merge-reject")
async def api_roster_merge_reject(payload: dict) -> Any:
    """Mark two subjects as NOT the same so they're never suggested for merge again."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    backend.roster.reject_merge(str(payload.get("a", "")), str(payload.get("b", "")))
    return {"ok": True}


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


@app.get("/api/detection/filters")
async def api_detection_filters() -> Any:
    """The operator's per-class DETECTION toggles (person / vehicle / animal / weapon /
    motion / track). Persisted server-side so disabling a class survives restarts and
    actually drops it from the processing budget."""
    return backend.get_detection_filters() if backend else {}


@app.post("/api/detection/filters")
async def api_detection_filters_set(payload: dict) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    return backend.set_detection_filters(payload or {})


@app.get("/api/plates")
async def api_plates_list() -> Any:
    """The plate watchlist — reading any of these on any camera raises a PLATE WATCHLIST HIT."""
    return {"plates": backend.list_watched_plates() if backend else []}


@app.post("/api/plates")
async def api_plates_watch(payload: dict) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    plate = str(payload.get("plate", "")).strip()
    if not plate:
        return JSONResponse({"error": "no plate"}, status_code=400)
    return {"plates": backend.watch_plate(plate, bool(payload.get("on", True)))}


@app.get("/api/spatial/{sid}")
async def api_spatial(sid: str, grid: int = 320) -> Any:
    """Spatial 3D scene: a monocular-depth point cloud of the camera's current frame.
    Always 200 with {"scene": {...}} or {"scene": null, "reason": "..."}."""
    if backend is None:
        return {"scene": None, "reason": "backend_down"}
    return await asyncio.to_thread(backend.spatial_scene, sid, grid)


@app.get("/api/spatial/reel/{sid}")
async def api_spatial_reel(sid: str, n: int = 28, grid: int = 256) -> Any:
    """HoloReel: capture N distinct frames spread over a few seconds, reconstruct 3D for each -> {"frames": [...]}."""
    if backend is None:
        return {"frames": [], "reason": "backend_down"}
    return await asyncio.to_thread(backend.spatial_reel, sid, n, grid)


# -- EARDRUM: sub-pixel surface motion as a vibration channel -------------------------------
@app.get("/api/probes/{sid}")
async def api_probes(sid: str) -> Any:
    if backend is None:
        return {"probes": []}
    return await asyncio.to_thread(backend.probes_list, sid)


@app.post("/api/probes/{sid}")
async def api_probe_add(sid: str, payload: dict) -> Any:
    """Place a listening probe. Refused with a reason when the surface has no texture to track."""
    if backend is None:
        return {"probe": None, "reason": "backend_down"}
    return await asyncio.to_thread(backend.probe_add, sid, payload.get("roi") or [],
                                   payload.get("name"), payload.get("kind"))


@app.put("/api/probes/{pid}")
async def api_probe_update(pid: int, payload: dict) -> Any:
    if backend is None:
        return {"probe": None}
    return await asyncio.to_thread(backend.probe_update, pid, payload)


@app.delete("/api/probes/{pid}")
async def api_probe_delete(pid: int) -> Any:
    if backend is None:
        return {"ok": False}
    return await asyncio.to_thread(backend.probe_delete, pid)


@app.get("/api/probes/{pid}/spectrum")
async def api_probe_spectrum(pid: int) -> Any:
    """The averaged spectrum, its measured noise floor, the peaks and the machinery hypothesis."""
    if backend is None:
        return {"spectrum": None}
    return await asyncio.to_thread(backend.probe_spectrum, pid)


@app.get("/api/probes/{pid}/trend")
async def api_probe_trend(pid: int, hours: int = 168) -> Any:
    if backend is None:
        return {"trend": []}
    return await asyncio.to_thread(backend.probe_trend, pid, hours)


@app.post("/api/probes/{pid}/baseline")
async def api_probe_baseline(pid: int) -> Any:
    """Freeze today's spectrum as the reference every later reading is compared against."""
    if backend is None:
        return {"ok": False}
    return await asyncio.to_thread(backend.probe_baseline, pid)


@app.get("/api/probes/{pid}/wave")
async def api_probe_wave(pid: int, seconds: float = 8.0) -> Response:
    """A band-limited WAV of the recovered displacement. Not an audio recording: the structural
    band cannot carry intelligible speech, and that is enforced in code."""
    if backend is None:
        return Response(status_code=503)
    data = await asyncio.to_thread(backend.probe_wave, pid, seconds)
    if not data:
        return Response(status_code=204)
    return Response(content=data, media_type="audio/wav",
                    headers={"Cache-Control": "no-store"})


@app.post("/api/eardrum/{sid}/suggest")
async def api_eardrum_suggest(sid: str, payload: dict | None = None) -> Any:
    """Rank candidate ROIs by trackability. An operator cannot see texture, and a probe on a
    blank wall returns noise forever without ever saying so."""
    if backend is None:
        return {"candidates": []}
    return await asyncio.to_thread(backend.eardrum_suggest, sid, int((payload or {}).get("n", 5)))


@app.get("/api/eardrum/{sid}/modal")
async def api_eardrum_modal(sid: str) -> Any:
    """Natural frequencies, damping and mode shapes across three or more probes."""
    if backend is None:
        return {"modes": [], "reason": "backend_down"}
    return await asyncio.to_thread(backend.eardrum_modal, sid)


@app.post("/api/eardrum/{sid}/calibrate")
async def api_eardrum_calibrate(sid: str) -> Any:
    """Solve the rolling-shutter line rate from mains flicker, so the acoustic band becomes
    available. Fails honestly when the scene has no flicker."""
    if backend is None:
        return {"ok": False, "reason": "backend_down"}
    return await asyncio.to_thread(backend.eardrum_calibrate, sid)


# -- BEDROCK: the past as a database, with provenance and two time axes ---------------------
@app.post("/api/bedrock/query")
async def api_bedrock_query(payload: dict) -> Any:
    """Run a typed query AST. Never 5xx: a refusal comes back as {error, clause, hint} so the
    UI can offer the fix rather than a stack trace."""
    if backend is None:
        return {"entities": [], "facts": [], "error": "backend_down"}
    return await asyncio.to_thread(backend.bedrock_query, payload)


@app.post("/api/ai/bedrock")
async def api_ai_bedrock(payload: dict[str, str]) -> Any:
    """Plain language into a BEDROCK query AST. The model never emits SQL, and the AST is
    rendered back to the operator as chips before it runs."""
    if backend is None or not backend.ai.enabled:
        return {"query": None, "disabled": True}
    vocab = await asyncio.to_thread(backend.bedrock_vocab)
    q = await asyncio.to_thread(backend.ai.plan_bedrock, payload.get("text", ""), vocab,
                                time.time() * 1000.0)
    if q is None:
        return {"query": None, "say": "I could not turn that into a query I can run."}
    return {"query": q, "say": q.pop("say", None) if isinstance(q, dict) else None}


@app.get("/api/bedrock/vocab")
async def api_bedrock_vocab() -> Any:
    """The closed predicate vocabulary the chip builder and the LLM planner may use."""
    if backend is None:
        return {"version": 0, "predicates": [], "kinds": []}
    return await asyncio.to_thread(backend.bedrock_vocab)


@app.get("/api/bedrock/stats")
async def api_bedrock_stats() -> Any:
    if backend is None:
        return {"facts": 0, "entities": 0, "oldest": None,
                "backfill": {"running": False, "done": 0, "total": 0, "phase": ""}}
    return await asyncio.to_thread(backend.bedrock_stats)


@app.post("/api/bedrock/backfill")
async def api_bedrock_backfill() -> Any:
    if backend is None:
        return {"started": False}
    return await asyncio.to_thread(backend.bedrock_backfill)


@app.get("/api/bedrock/entity/{uid}")
async def api_bedrock_entity(uid: int) -> Any:
    """One entity, everything currently believed about it, and everything ever believed."""
    if backend is None:
        return {"entity": None, "current": [], "history": []}
    return await asyncio.to_thread(backend.bedrock_entity, uid)


@app.get("/api/bedrock/fact/{fact_id}/provenance")
async def api_bedrock_provenance(fact_id: int) -> Any:
    """Which model asserted this, from which frame, and what it replaced."""
    if backend is None:
        return {"fact": None, "lineage": []}
    return await asyncio.to_thread(backend.bedrock_provenance, fact_id)


@app.post("/api/bedrock/purge/{uid}")
async def api_bedrock_purge(uid: int) -> Any:
    """Hard erasure of one individual's entire record. Irreversible by design."""
    if backend is None:
        return {"facts": 0, "entities": 0, "snapshots": 0}
    return await asyncio.to_thread(backend.bedrock_purge, uid)


# -- DREAMSTATE: what this place normally looks like, and where reality departs from it ------
@app.get("/api/dream/divergences")
async def api_dream_divergences(sid: str | None = None, limit: int = 100) -> Any:
    """Fired divergences, newest first. Declared BEFORE /api/dream/{sid} so the literal path
    wins the route match."""
    if backend is None:
        return {"divergences": []}
    return await asyncio.to_thread(backend.dream_divergences, sid, limit)


@app.post("/api/dream/divergence/{div_id}/verdict")
async def api_dream_verdict(div_id: int, payload: dict | None = None) -> Any:
    if backend is None:
        return {"ok": False}
    return await asyncio.to_thread(backend.dream_verdict, div_id, (payload or {}).get("verdict"))


@app.get("/api/dream/{sid}")
async def api_dream(sid: str) -> Any:
    """Live expectation state: per-cell sigma, per-bucket maturity, muted cells."""
    if backend is None:
        return {"status": None, "reason": "backend_down"}
    return await asyncio.to_thread(backend.dream_status, sid)


@app.get("/api/dream/{sid}/plate")
async def api_dream_plate(sid: str) -> Response:
    """The learned background plate: what this camera expects to be there. 204 until warm."""
    if backend is None:
        return Response(status_code=503)
    data = await asyncio.to_thread(backend.dream_plate, sid)
    if not data:
        return Response(status_code=204)
    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/dream/{sid}/pulse")
async def api_dream_pulse(sid: str, hours: int = 24) -> Any:
    if backend is None:
        return {"pulse": []}
    return await asyncio.to_thread(backend.dream_pulse, sid, hours)


@app.post("/api/dream/{sid}/mute")
async def api_dream_mute(sid: str, payload: dict | None = None) -> Any:
    """Silence the flag that flaps and the tree that sways."""
    if backend is None:
        return {"muted": []}
    p = payload or {}
    cells = [int(c) for c in (p.get("cells") or [])]
    return await asyncio.to_thread(backend.dream_mute, sid, cells,
                                   int(p.get("from_hour", 0)), int(p.get("to_hour", 24)))


@app.post("/api/dream/{sid}/threshold")
async def api_dream_threshold(sid: str, payload: dict | None = None) -> Any:
    if backend is None:
        return {"threshold": 5.0}
    return await asyncio.to_thread(backend.dream_threshold, sid,
                                   float((payload or {}).get("sigma", 5.0)))


@app.post("/api/dream/{sid}/reset")
async def api_dream_reset(sid: str, payload: dict | None = None) -> Any:
    """`reregister` tries to realign against the stored plate; `relearn` forgets the camera."""
    if backend is None:
        return {"ok": False}
    return await asyncio.to_thread(backend.dream_reset, sid,
                                   str((payload or {}).get("mode", "relearn")))


# -- FOG OF WAR: the observability field and its complement ---------------------------------
@app.get("/api/coverage/{sid}")
async def api_coverage(sid: str, task: str | None = None, height: float | None = None) -> Any:
    """What this camera can and cannot see, right now. Always 200 with
    {"coverage": {...}} or {"coverage": null, "reason": "..."}."""
    if backend is None:
        return {"coverage": None, "reason": "backend_down"}
    return await asyncio.to_thread(backend.coverage_scene, sid, task, height)


@app.get("/api/coverage/{sid}/blindspots")
async def api_blind_spots(sid: str) -> Any:
    """Persistent blind spots, ranked by area times traffic times losses."""
    if backend is None:
        return {"spots": []}
    return await asyncio.to_thread(backend.blind_spots, sid)


@app.post("/api/blindspots/{spot_id}/dismiss")
async def api_dismiss_blind_spot(spot_id: int, payload: dict | None = None) -> Any:
    if backend is None:
        return {"ok": False}
    on = bool((payload or {}).get("on", True))
    return await asyncio.to_thread(backend.dismiss_blind_spot, spot_id, on)


@app.get("/api/coverage/{sid}/report")
async def api_coverage_report(sid: str) -> Any:
    """A printable coverage statement: task, percentage, DORI bands, blind spots, methodology."""
    if backend is None:
        return {"report": None, "reason": "backend_down"}
    return await asyncio.to_thread(backend.coverage_report, sid)


# -- GRAIN: the behavioural grain of the place (movement only, never appearance) ------------
@app.get("/api/grain/{sid}")
async def api_grain(sid: str, bucket: int | None = None, cls: str = "person") -> Any:
    """The learned movement field for one time bucket: per-cell heading roses, speeds and how
    strongly the place prefers a direction."""
    if backend is None:
        return {"status": None, "reason": "backend_down"}
    return await asyncio.to_thread(backend.grain_field, sid, bucket, cls)


@app.get("/api/grain/{sid}/tracks")
async def api_grain_tracks(sid: str, limit: int = 100, unusual: int = 0) -> Any:
    if backend is None:
        return {"tracks": []}
    return await asyncio.to_thread(backend.grain_ledger, sid, limit, bool(unusual))


@app.get("/api/grain/track/{track_id}/precedents")
async def api_grain_precedents(track_id: int, n: int = 6) -> Any:
    """The closest historical trajectories by shape. "The last three times someone did this it
    was the courier" is worth more to an operator than a confidence number."""
    if backend is None:
        return {"precedents": []}
    return await asyncio.to_thread(backend.grain_precedents, track_id, n)


@app.post("/api/grain/track/{track_id}/verdict")
async def api_grain_verdict(track_id: int, payload: dict | None = None) -> Any:
    if backend is None:
        return {"ok": False}
    verdict = (payload or {}).get("verdict")
    return await asyncio.to_thread(backend.grain_verdict, track_id, verdict)


@app.post("/api/grain/{sid}/mute")
async def api_grain_mute(sid: str, payload: dict | None = None) -> Any:
    """Paint cells out of scoring (the doorway where staff always loiter)."""
    if backend is None:
        return {"muted": []}
    p = payload or {}
    cells = [int(c) for c in (p.get("cells") or [])]
    return await asyncio.to_thread(backend.grain_mute, sid, cells, bool(p.get("on", True)))


# -- long-term identity: subjects / dossiers / reconstruction (features 5/6/7) --------------
@app.get("/api/subjects")
async def api_subjects(cls: str | None = None, limit: int = 200, order: str = "last_seen") -> Any:
    """Persisted long-term subjects (repeat visitors), most recently seen first."""
    if backend is None:
        return []
    return await asyncio.to_thread(backend.subjects_list, cls, limit, order)


@app.get("/api/subjects/{sid}/dossier")
async def api_subject_dossier(sid: int) -> Any:
    """A subject's dossier: per-camera + hour-of-day patterns and the full sighting history.
    Always 200 with {"dossier": {...}} or {"dossier": null}."""
    if backend is None:
        return {"dossier": None}
    return {"dossier": await asyncio.to_thread(backend.subject_dossier, sid)}


@app.get("/api/subjects/{sid}/reconstruct")
async def api_subject_reconstruct(sid: int) -> Any:
    """Multi-frame super-resolution of the subject from its sighting crops (feature 7)."""
    if backend is None:
        return {"image": None, "reason": "backend_down"}
    return await asyncio.to_thread(backend.reconstruct_subject, sid)


@app.get("/api/reconstruct/plate/{det_id}")
async def api_reconstruct_plate(det_id: str) -> Any:
    """Fuse a vehicle track's plate crops into one super-res plate and re-read it (feature 7)."""
    if backend is None:
        return {"image": None, "reason": "backend_down"}
    return await asyncio.to_thread(backend.reconstruct_plate, det_id)


@app.get("/api/suggestions")
async def api_suggestions() -> Any:
    """Proactive smart suggestions — alert rules to add and camera improvements."""
    if backend is None:
        return {"suggestions": []}
    return {"suggestions": await asyncio.to_thread(backend.build_suggestions)}


@app.post("/api/alerts/rules")
async def api_add_alert_rule(payload: dict) -> Any:
    """Create an alert rule (used by one-click suggestion acceptance) and refresh the engine."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    rid = backend.db.add_alert_rule(
        str(payload.get("name", "RULE")), str(payload["event_type"]),
        source_id=payload.get("source_id"), severity=str(payload.get("severity", "warning")),
        min_count=payload.get("min_count"), cooldown_s=float(payload.get("cooldown_s", 60.0)))
    backend.alert_engine.set_rules(backend.db.list_alert_rules())
    return {"id": rid}


@app.get("/api/cameras/dna")
async def api_camera_dna() -> Any:
    """Per-camera DNA (behavioural tags) and reputation (reliability score)."""
    if backend is None:
        return {"cameras": []}
    return {"cameras": await asyncio.to_thread(backend.camera_dna)}


@app.get("/api/relationships")
async def api_relationships() -> Any:
    """The social graph — subjects and their discovered co-occurrence associations."""
    if backend is None:
        return {"nodes": [], "edges": []}
    return await asyncio.to_thread(backend.relationship_graph)


@app.get("/api/roster/{det_id}/relationships")
async def api_entity_relationships(det_id: str) -> Any:
    """Subjects most associated with this one (frequently seen together)."""
    if backend is None:
        return {"associates": []}
    assoc = await asyncio.to_thread(backend.entity_relationships, det_id)
    return {"associates": assoc}


@app.get("/api/roster/{det_id}/graph")
async def api_entity_graph(det_id: str) -> Any:
    """The subject's 2-hop relationship network for the profile page (subject → contacts →
    their contacts)."""
    if backend is None:
        return {"center": det_id, "nodes": [], "edges": []}
    return await asyncio.to_thread(backend.entity_ego_graph, det_id)


@app.post("/api/roster/{det_id}/find")
async def api_roster_find(det_id: str) -> Any:
    """Find this roster subject across all cameras by appearance (ReID) — returns scored hits."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    matches = await asyncio.to_thread(backend.find_across, det_id)
    return {"matches": matches}


@app.get("/api/roster/{det_id}/supercut")
async def api_roster_supercut(det_id: str) -> Any:
    """Build (and cache) a subject's journey supercut — their per-camera clips stitched in
    order — and return its URL. 404 until at least one leg has been clipped."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    url = await asyncio.to_thread(backend.build_supercut, det_id)
    return {"url": url} if url else JSONResponse({"error": "no clips yet"}, status_code=404)


@app.get("/api/roster/{det_id}/cutout")
async def api_roster_cutout(det_id: str) -> Response:
    """The roster photo with its background removed (YOLO-seg), as a transparent PNG."""
    if backend is None:
        return Response(status_code=503)
    png = await asyncio.to_thread(backend.roster.cutout_png, det_id)
    if not png:
        return Response(status_code=404)
    return Response(content=png, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.get("/api/roster/{det_id}/face")
async def api_roster_face(det_id: str) -> Response:
    """The roster photo cropped to the subject's face (portrait). 404 when no face is found so
    the UI falls back to the full snapshot."""
    if backend is None:
        return Response(status_code=503)
    jpg = await asyncio.to_thread(backend.roster.face_png, det_id)
    if not jpg:
        return Response(status_code=404)
    return Response(content=jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


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
    names = {s.id: s.name for s in backend.db.list_sources()}
    return [
        {"id": a.id, "ts": a.timestamp * 1000, "severity": a.severity, "type": a.event_type,
         "summary": a.summary, "cam": names.get(a.source_id, ""), "ack": a.acknowledged,
         "snapshot": _snap_url(a.snapshot_path), "clip": a.clip_path}
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


def _scan_stats(root: Path) -> tuple[int, int]:
    """Fast recursive (count, bytes) via os.scandir — the size comes free from the directory
    enumeration on Windows, so there is no extra stat() syscall per file. Walking hundreds of
    thousands of snapshots with Path.rglob()+stat() froze the event loop for seconds; this does not."""
    n = sz = 0
    stack = [str(root)]
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            n += 1
                            sz += e.stat(follow_symlinks=False).st_size
                    except OSError:
                        pass
        except OSError:
            pass
    return n, sz


def _compute_storage(bk: Backend) -> dict[str, Any]:
    db = bk.db
    try:
        recent = [
            {"kind": r.kind, "start": r.start_ts * 1000, "end": r.end_ts * 1000,
             "sizeMB": round((r.size_bytes or 0) / 1e6, 1), "mode": r.mode}
            for r in db.list_recordings(limit=30)
        ]
    except Exception:  # noqa: BLE001
        recent = []
    snap_root = bk.data_dir / "snapshots"
    clip_n, clip_sz = _scan_stats(snap_root / "clips")
    all_n, all_sz = _scan_stats(snap_root)
    snap_n, snap_sz = max(0, all_n - clip_n), max(0, all_sz - clip_sz)
    rec_sz = db.total_recordings_size() or 0
    return {
        "recordings": db.count_recordings(),
        "sizeGB": round((rec_sz + all_sz) / 1e9, 2),          # total disk: recordings + clips + snapshots
        "recGB": round(rec_sz / 1e9, 2),
        "snapshots": snap_n, "snapshotsMB": round(snap_sz / 1e6, 1),
        "clips": clip_n, "clipsMB": round(clip_sz / 1e6, 1),
        "oldest": (db.oldest_recording_ts() or 0) * 1000,
        "recent": recent,
    }


_STORAGE_CACHE: dict[str, Any] = {"ts": 0.0, "data": None}
_STORAGE_TTL = 300.0  # snapshots grow slowly; a 5-min cache keeps the endpoint instant


@app.get("/api/storage")
async def api_storage() -> Any:
    if backend is None:
        return {}
    cached = _STORAGE_CACHE["data"]
    if cached is not None and (time.time() - _STORAGE_CACHE["ts"]) < _STORAGE_TTL:
        return cached
    # Compute off the event loop so the (potentially large) filesystem walk never blocks streaming.
    data = await asyncio.to_thread(_compute_storage, backend)
    _STORAGE_CACHE["data"] = data
    _STORAGE_CACHE["ts"] = time.time()
    return data


@app.get("/api/cases")
async def api_cases() -> Any:
    if backend is None:
        return []
    return [
        {"id": c.id, "name": c.name, "threat": c.threat_level, "notes": c.notes,
         "status": c.status, "created": c.created_at * 1000,
         "targets": len(backend.db.list_case_targets(c.id))}
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


@app.post("/api/cases/from-alert")
async def api_case_from_alert(payload: dict) -> Any:
    """Open an investigation case seeded with an alert as its incident. Returns the case id."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    alert = payload.get("alert") or payload
    cid = await asyncio.to_thread(backend.open_case_from_alert, alert)
    return {"id": cid}


@app.get("/api/cases/{case_id}")
async def api_case_detail(case_id: int) -> Any:
    """A case as an investigation: incident, timeline of scene events around it, and AI summary."""
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    detail = await asyncio.to_thread(backend.case_detail, case_id)
    return detail if detail else JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/cases/{case_id}/status")
async def api_case_status(case_id: int, payload: dict) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    backend.db.set_case_status(case_id, str(payload.get("status", "open")))
    return {"ok": True}


@app.put("/api/cases/{case_id}")
async def api_case_update(case_id: int, payload: dict) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    c = backend.db.get_case(case_id)
    if c is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    backend.db.update_case(case_id, payload.get("name", c.name),
                           payload.get("threat", c.threat_level), payload.get("notes", c.notes))
    return {"ok": True}


@app.delete("/api/cases/{case_id}")
async def api_case_delete(case_id: int) -> Any:
    if backend is None:
        return JSONResponse({"error": "backend down"}, status_code=503)
    backend.db.delete_case(case_id)
    return {"ok": True}


# Serve alert/snapshot images (must be mounted before the "/" catch-all).
from core.config import load_config as _load_config  # noqa: E402
_SNAP = Path(str(_load_config(Path("config/default.yaml")).get("app.data_dir", "data"))) / "snapshots"
_SNAP.mkdir(parents=True, exist_ok=True)
app.mount("/snapshots", StaticFiles(directory=str(_SNAP)), name="snaps")

# Serve the built front-end last (so /api and /ws win). Optional.
_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="web")
