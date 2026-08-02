"""LLM assistant layer — OpenAI-compatible (GLM / Z.AI by default, but any
OpenAI-standard endpoint works). Powers natural-language search, event summaries,
anomaly explanations and VLM scene description. Config from config/ai_secret.json
or OVERSEER_AI_* env vars; disabled gracefully when no key is present."""
from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

import requests

log = logging.getLogger("overseer.ai")

_CFG_PATHS = [
    os.environ.get("OVERSEER_AI_CONFIG", ""),
    str(Path(__file__).resolve().parent.parent / "config" / "ai_secret.json"),
]

# Every LLM-powered capability is an independently switchable feature. The operator can
# turn any of them off from the assistant settings; when the provider is unreachable or
# unconfigured they are all inert. In BOTH cases the surveillance system keeps running
# exactly as before — nothing here is on the detection/alert critical path.
# Event types an operator can build a rule around (behavioural + presence). Kept in
# sync with events.types.EventType; make_rule validates the model's choice against this.
_RULE_EVENTS = [
    "MOTION", "PERSON", "VEHICLE", "ANIMAL", "RESTRICTED", "LOITERING", "LINE_CROSS",
    "RUNNING", "STOPPED", "U_TURN", "WRONG_DIRECTION", "TAILGATING", "QUEUE", "FALLING",
    "CROWDING", "FIGHTING", "ABANDONED_OBJECT", "REMOVED_OBJECT",
    "DEFOCUS", "OBSTRUCTION", "CAMERA_MOVED", "ANOMALY",
]

DEFAULT_FEATURES: dict[str, bool] = {
    "chat": True,        # free-form assistant chat
    "search": True,      # natural-language → forensic filter (catalog 89)
    "summarize": True,   # event / shift summary (catalog 227/228)
    "explain": True,     # alert "why this matters" (catalog 68/230)
    "vision": True,      # VLM scene description (catalog 231)
    "rules": True,       # natural-language → alert rule (catalog 201-203 via LLM)
    "correlate": True,   # multi-alert correlation & triage (idea 5)
    "advise": True,      # recommended action / SOP per alert (idea 7)
    "semantic": True,    # semantic search over the event timeline (idea 10)
    "operate": True,     # AI Operator: natural-language command → chain of system actions
}

# The whole controllable surface the AI Operator can drive. Mirrors the frontend action
# registry (web/src/lib/operator.ts ACTIONS); keep the two in sync.
_OPERATOR_ACTIONS = (
    "open_screen {name} — name is one of: roster, forensic, watchlist, suggestions, spatial, "
    "storage, topology, archive, case, zones, rules, alerts, assistant, command, map, montage, pov.\n"
    "switch_camera {name} — make the named camera the active live feed.\n"
    "side_by_side {cameras?} — multi-camera live wall; cameras is an optional list of names.\n"
    "forensic_search {query, time?} — appearance search; query like 'grey car', time one of 1h/24h/7d.\n"
    "find_watched {} — show red-flagged / BOLO subjects in the roster.\n"
    "filter_roster {cls?, color?, subtype?, height?, watched?, query?} — open the roster and apply "
    "detailed filters: cls person/vehicle, color (black/white/gray/red/blue/green/yellow/brown/…), "
    "subtype (car/truck/bus/motorcycle/van), height (short/medium/tall), watched true, or free text. "
    "'show watched red trucks' -> filter_roster{cls:'vehicle',color:'red',subtype:'truck',watched:true}.\n"
    "search_forensic {kind?, color?, height?, subtype?, make?, query?, time?} — detailed appearance "
    "search across the whole record; time is 1h/24h/7d. Prefer this over forensic_search for anything "
    "with colour/type/make/height.\n"
    "count {cls} — answer how many are on the ACTIVE camera right now; cls is person/vehicle/any. "
    "To count on another camera, chain switch_camera first, then count.\n"
    "count_alerts {severity?} — answer how many alerts are active (optionally critical/warning/info).\n"
    "find_subject {cls?, camera?, color?} — find the most recent matching subject (person/vehicle) "
    "in the roster; give the step an \"as\" name so later steps can use it.\n"
    "watch_subject {subject, name?} — add a found subject to the watchlist and optionally name it.\n"
    "super_fuse {subject} — enhance / clarify a subject's photo (super-resolution).\n"
    "last_seen {subject} — answer when a subject was last seen.\n"
    "describe_scene {camera?} — describe what a camera currently sees.\n"
    "ask_vision {question, camera?} — LOOK at the live frame and answer a VISUAL question about what "
    "is on screen (colour, an object, what someone is holding, the make of a visible car, what is "
    "behind someone). Use this for ANY question about what is currently visible, especially details "
    "we do not store as data.\n"
    "create_case {name} — open a new investigation case.\n"
    "create_alert_rule {text} — create a STANDING alert rule from a natural-language instruction "
    "(e.g. 'alarm on a vehicle at night'); NEVER an immediate one-off alarm.\n"
    "set_module {key, on} — toggle ANY module/overlay (every checkbox): detection (weapon, person, "
    "vehicle, animal, motion, tracking), environment (daynight, fog, rain, wind, weather), sky "
    "(lightning, meteor, satellite), visual overlays (heatmap, tactical, foresight, tracklet). "
    "'alarm if you see a weapon' -> set_module{key:'weapon',on:true} (it then auto-alerts). 'show the "
    "heatmap' -> set_module{key:'heatmap',on:true}.\n"
    "watch_plate {plate} — add a licence plate to the BOLO list; a re-read on any camera alerts.\n"
    "zoom {level, x?, y?} — digital PTZ zoom on the live feed (level 1-5, 1 resets).\n"
    "reset_view {} — reset the digital zoom/pan.\n"
    "next_camera {dir?} — go to the next (dir 1) or previous (dir -1) camera.\n"
    "go_home {} — return to the camera map.\n"
    "fullscreen {on} — enter/exit fullscreen.\n"
    "mute {on} — mute/unmute the interface audio.\n"
    "acknowledge_alerts {} — acknowledge all active alerts.\n"
    "summarize {} — answer with a short briefing of recent activity.\n"
    "correlate_alerts {} — answer whether recent alerts form one incident.\n"
    "camera_status {camera?} — answer a camera's live fps/brightness and current people/vehicle count.\n"
    "camera_dna {camera?} — answer a camera's learned profile (busy/quiet/night/etc.) and reputation.\n"
    "system_status {} — answer CPU/GPU/RAM/storage load.\n"
    "storage_status {} — answer recordings count and disk used.\n"
    "list_cameras {} — answer the list of camera names.\n"
    "offline_cameras {} — answer which cameras are offline.\n"
    "busiest_camera {go?} — answer which camera has the most activity; go:true also switches to it.\n"
    "pan {dir} — digital pan the live feed (left/right/up/down).\n"
    "latest_alert {} — answer what the most recent alert is.\n"
    "explain_alert {} — answer why the latest alert matters.\n"
    "advise_alert {} — answer a recommended action for the latest alert.\n"
    "case_from_alert {} — open an investigation case from the latest alert.\n"
    "search_events {query} — answer a question by semantically searching the event timeline.\n"
    "count_subjects {cls?, color?, subtype?} — answer how many matching subjects were seen this session.\n"
    "list_watched {} — answer who is on the watchlist.\n"
    "clear_zones {} — remove all drawn zones.\n"
    "find_plate {plate} — search the record for a licence plate.\n"
    "stats {period?} — answer the top event types over the last day (or 'week').\n"
    "help {} — tell the operator what you can do.\n"
    "mark_false {} — mark the latest alert as a false alarm (trains suppression).\n"
    "unwatch_subject {subject} — remove a subject from the watchlist.\n"
    "relationships {subject} — answer who a subject is usually seen with.\n"
    "alerts_here {camera?} — answer how many active alerts a camera has.\n"
    "close_panels {} — close any open overlays/panels.\n"
    "reconnect {} — reconnect the active camera feed.\n"
    "open_case {id?, name?} — open an investigation case (by id or name), or the case workspace.\n"
    "list_cases {} — answer the open investigation cases.\n"
    "summarize_case {id?} — summarize a case (the open one if no id given).\n"
    "alerts_with_clips {} — open the alerts board and answer how many alerts have a replay clip.\n"
    "track_object {} — open the draw-a-box tool to track an arbitrary object.\n"
    "find_pet {} — open the pet finder.\n"
    "open_spatial {camera?} — open the 3D spatial scene for a camera.\n"
    "timeline {on?} — show/hide the event timeline.\n"
    "list_plates {} — answer which licence plates are on the BOLO list.\n"
    "unwatch_plate {plate} — remove a plate from the BOLO list.\n"
    "list_zones {} — answer the zones currently drawn.\n"
    "quietest_camera {} — answer which camera has the least activity.\n"
    "night_cameras {} — answer which cameras are night-dominant.\n"
    "flagged_cameras {} — answer which cameras have low detection quality.\n"
    "where_seen {subject} — answer the camera trail a subject was seen on.\n"
    "locate {subject} — find where a subject is now and switch to that camera.\n"
    "repeat_last {} — repeat the last answer.\n"
    "narrate {on} — live narration: continuously describe the active camera aloud (needs a vision model).\n"
    "follow {on} — follow-cam: keep the locked target centred with digital PTZ (lock a target first).\n"
    "xray {on} — occlusion x-ray: keep tracking a subject that goes behind cover.\n"
    "enhance {} — start the box-select 'enhance' tool (the operator then drags a box to clarify it).\n"
    "say {text} — just speak a reply, for questions that need no action."
)


class LLMClient:
    def __init__(self) -> None:
        self.base = ""
        self.key = ""
        self.model = "gpt-4o-mini"
        self.vision_model = ""
        self.provider = "openai"
        self.features: dict[str, bool] = dict(DEFAULT_FEATURES)
        self._load()

    def _load(self) -> None:
        cfg: dict = {}
        for p in _CFG_PATHS:
            if p and os.path.exists(p):
                try:
                    cfg = json.loads(Path(p).read_text(encoding="utf-8"))
                    break
                except Exception:  # noqa: BLE001
                    pass
        self.base = (os.environ.get("OVERSEER_AI_BASE") or cfg.get("base_url") or "").rstrip("/")
        self.key = os.environ.get("OVERSEER_AI_KEY") or cfg.get("api_key") or ""
        self.model = os.environ.get("OVERSEER_AI_MODEL") or cfg.get("model") or self.model
        self.vision_model = cfg.get("vision_model") or self.model
        self.provider = cfg.get("provider") or "openai"
        # Feature flags: start from defaults, apply saved overrides (unknown keys ignored).
        feats = dict(DEFAULT_FEATURES)
        saved = cfg.get("features")
        if isinstance(saved, dict):
            for k in DEFAULT_FEATURES:
                if k in saved:
                    feats[k] = bool(saved[k])
        self.features = feats

    def reload(self) -> None:
        self._load()

    @property
    def enabled(self) -> bool:
        return bool(self.base and self.key)

    def feature(self, name: str) -> bool:
        """True only if the provider is usable AND this capability is switched on.
        Every LLM entry point checks this first, so a disabled feature (or an absent
        provider) is a graceful no-op rather than an error."""
        return self.enabled and bool(self.features.get(name, False))

    def _key_hint(self) -> str:
        """Masked tail of the stored key so the UI can confirm one is set."""
        k = self.key
        return f"…{k[-4:]}" if len(k) >= 8 else ("set" if k else "")

    def status(self) -> dict:
        return {"enabled": self.enabled, "provider": self.provider, "model": self.model,
                "base": self.base, "vision": bool(self.vision_model),
                "vision_model": self.vision_model, "keyHint": self._key_hint(),
                "features": dict(self.features)}

    def save_config(self, cfg: dict) -> dict:
        """Persist provider/base_url/api_key/model to config/ai_secret.json and reload.
        An empty api_key keeps the existing key (so the UI can re-save without
        re-typing the secret). Returns the new status()."""
        path = Path(__file__).resolve().parent.parent / "config" / "ai_secret.json"
        cur: dict = {}
        if path.exists():
            try:
                cur = json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                cur = {}
        for k in ("provider", "base_url", "model", "vision_model"):
            v = cfg.get(k)
            if v is not None:
                cur[k] = str(v).strip()
        # Merge feature toggles (only known keys, coerced to bool).
        feats = cfg.get("features")
        if isinstance(feats, dict):
            stored = dict(cur.get("features") or {})
            for k in DEFAULT_FEATURES:
                if k in feats:
                    stored[k] = bool(feats[k])
            cur["features"] = stored
        new_key = (cfg.get("api_key") or "").strip()
        if new_key and not set(new_key) <= {"•", "*", "…"}:  # ignore a masked placeholder
            cur["api_key"] = new_key
        if cur.get("base_url"):
            cur["base_url"] = cur["base_url"].rstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cur, indent=2), encoding="utf-8")
        # env overrides would shadow the file; drop them so the saved config takes effect.
        for e in ("OVERSEER_AI_BASE", "OVERSEER_AI_KEY", "OVERSEER_AI_MODEL"):
            os.environ.pop(e, None)
        self._load()
        return self.status()

    def test(self, cfg: dict | None = None) -> dict:
        """Ping the endpoint (optionally with a candidate config, without saving).
        Returns {ok, detail}."""
        base = ((cfg or {}).get("base_url") or self.base or "").rstrip("/")
        key = (cfg or {}).get("api_key") or ""
        if not key or set(key) <= {"•", "*", "…"}:
            key = self.key
        model = (cfg or {}).get("model") or self.model
        if not (base and key):
            return {"ok": False, "detail": "base URL and API key required"}
        try:
            r = requests.post(f"{base}/chat/completions",
                              headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                              json={"model": model, "messages": [{"role": "user", "content": "ping"}],
                                    "max_tokens": 5, "thinking": {"type": "disabled"}}, timeout=20.0)
            if r.status_code == 200:
                return {"ok": True, "detail": f"{model} reachable"}
            msg = ""
            try:
                msg = (r.json().get("error") or {}).get("message") or r.text[:120]
            except Exception:  # noqa: BLE001
                msg = r.text[:120]
            return {"ok": False, "detail": f"HTTP {r.status_code}: {msg}"}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "detail": str(exc)[:160]}

    def _post(self, payload: dict, timeout: float = 60.0) -> str | None:
        if not self.enabled:
            return None
        try:
            r = requests.post(f"{self.base}/chat/completions",
                              headers={"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"},
                              json=payload, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            return (data.get("choices") or [{}])[0].get("message", {}).get("content") or None
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM call failed: %s", str(exc)[:200])
            return None

    def chat(self, prompt: str, system: str | None = None, max_tokens: int = 700,
             temperature: float = 0.4) -> str | None:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return self._post({"model": self.model, "messages": msgs, "max_tokens": max_tokens,
                           "temperature": temperature, "thinking": {"type": "disabled"}})

    def summarize(self, events: list[dict]) -> str | None:
        """Short natural-language incident/shift summary (kept terse)."""
        lines = [f"- {e.get('type','?')} @ {e.get('cam','?')} ({e.get('label','')})" for e in events[:40]]
        prompt = ("Summarize these surveillance events in 2-3 short sentences for an "
                  "operator. Be factual, no speculation, no identities.\n" + "\n".join(lines))
        return self.chat(prompt, system="You are a concise security operations assistant.", max_tokens=400)

    def explain(self, alert: dict) -> str | None:
        """One-sentence 'why this is flagged' explanation (terse)."""
        prompt = (f"In ONE short sentence, explain why this is worth an operator's attention: "
                  f"type={alert.get('type')}, detail={alert.get('summary')}, camera={alert.get('cam')}. "
                  f"No identities, no speculation beyond the data.")
        return self.chat(prompt, system="You are a concise security analyst.", max_tokens=200)

    @staticmethod
    def _json(out: str | None) -> dict | None:
        """Extract the first JSON object from an LLM reply, tolerating prose around it."""
        if not out:
            return None
        try:
            return json.loads(out[out.index("{"): out.rindex("}") + 1])
        except Exception:  # noqa: BLE001
            return None

    def query(self, text: str) -> dict | None:
        """Natural-language → structured forensic filter (kind/color/height/timeWindow)."""
        prompt = (
            "Convert this surveillance search request into JSON with keys: "
            'kind (one of person/vehicle/animal or ""), color (lowercase colour word or ""), '
            'height (short/medium/tall or ""), time (one of 1h/24h/7d or ""). '
            'Reply ONLY with the JSON object, nothing else.\nRequest: ' + text)
        return self._json(self.chat(prompt, system="You output only strict JSON.", max_tokens=300))

    def make_rule(self, text: str, cameras: list[dict] | None = None,
                  zones: list[dict] | None = None) -> dict | None:
        """Natural-language instruction → a validated alert-rule spec (idea 1). cameras and
        zones are [{id,name}] so the model can resolve names to ids. Returns None if the
        model can't produce a valid rule — the caller then simply doesn't create one."""
        cam_lines = "; ".join(f"{c.get('id')}={c.get('name')}" for c in (cameras or [])) or "none"
        zone_lines = "; ".join(f"{z.get('id')}={z.get('name')}" for z in (zones or [])) or "none"
        prompt = (
            "Convert this operator instruction into ONE alert rule as JSON with keys: "
            "name (short label), event_type (EXACTLY one of: " + ", ".join(_RULE_EVENTS) + "), "
            "source_id (camera id number, or null for all cameras), zone_id (zone id number or null), "
            "min_count (integer or null), min_confidence (0-1 or null), "
            "severity (info|warning|critical), cooldown_s (number, default 60). "
            f"Cameras id=name: {cam_lines}. Zones id=name: {zone_lines}. "
            "Choose the closest event_type. Reply ONLY with the JSON object.\nInstruction: " + text)
        rule = self._json(self.chat(prompt, system="You output only strict JSON for one security alert rule.",
                                    max_tokens=400))
        if not isinstance(rule, dict):
            return None
        et = str(rule.get("event_type") or "").upper()
        if et not in _RULE_EVENTS:
            return None
        rule["event_type"] = et
        sev = str(rule.get("severity") or "warning").lower()
        rule["severity"] = sev if sev in ("info", "warning", "critical") else "warning"
        return rule

    def plan_command(self, command: str, context: dict | None = None) -> dict | None:
        """AI Operator: turn a natural-language command into an ordered plan of system actions.
        Returns {steps:[{action,args}], say, ask, border}. `context` carries live grounding
        (camera names, active camera, current screen) so references like 'the store camera'
        resolve. Returns None when the model can't produce a plan (caller degrades gracefully)."""
        ctx = context or {}
        cams = ", ".join(str(c) for c in (ctx.get("cameras") or [])) or "none"
        active = ctx.get("active_camera") or "none"
        screen = ctx.get("mode") or "?"
        prompt = (
            "You are the planning brain of the AI Operator for a video-surveillance app. YOU decide "
            "how to fulfil the operator's request by decomposing it into an ordered CHAIN of actions "
            "from the list below. Do the WHOLE request, not just the first part — if it asks for "
            "several things, output a step for each, in the right order.\n\n"
            "ACTIONS (use only these):\n" + _OPERATOR_ACTIONS + "\n\n"
            'Reply with ONLY this JSON (no prose): {"steps":[{"action":"...","args":{...},"as":"name?"}], '
            '"say":"one short spoken confirmation or the answer", "border":"nav"|"alert"}.\n\n'
            "RULES:\n"
            "- Decompose fully. A request with 'and' / 'then' / 'after that' / commas / multiple verbs "
            "becomes multiple steps.\n"
            '- DATA PASSING: name a step\'s result with "as", then reference it later in args as "$name" '
            "(e.g. find something, then act on it).\n"
            "- CONDITIONALS ('if there is a car…'): just chain find_subject then the action on its "
            "result — if nothing is found the later steps simply report that. Do not invent a branch.\n"
            "- QUESTIONS about another camera: chain switch_camera first, then the query action.\n"
            "- VISUAL questions about what is on screen — what something IS, its colour/brand/price, "
            "what someone is holding, what is happening, ANY object even ones we don't detect (a "
            "boat, a sign, a bag, a building): the plan is ONE step, ask_vision{question:<the FULL "
            "question>}. NEVER use find_subject / super_fuse / describe_scene / enhance for these. "
            "Example: 'what is the price and brand of the boat at the dock?' => {\"steps\":[{\"action\""
            ":\"ask_vision\",\"args\":{\"question\":\"Estimate the brand and price of the boat at the "
            "dock.\"}}],\"border\":\"nav\"}. For another camera, chain switch_camera first.\n"
            "- Resolve names against the live context; NEVER invent a camera name. If a needed camera/"
            "target is truly unspecified and unguessable, return {\"ask\":\"...\",\"steps\":[]}.\n"
            "- border is \"alert\" only when creating an alarm/critical rule, else \"nav\".\n"
            "- If the operator asks HOW to do something, WHAT a feature is, or for help (not a command "
            'to run), return {"steps":[]} with no say — it is answered separately by the app guide.\n\n'
            "EXAMPLES:\n"
            'Req: "go to the street cam, if there is a car add it to the watchlist as \'car\', enhance '
            'its photo, and tell me when it was last seen"\n'
            '=> {"steps":[{"action":"switch_camera","args":{"name":"Street Cam"}},'
            '{"action":"find_subject","args":{"cls":"vehicle","camera":"Street Cam"},"as":"car"},'
            '{"action":"watch_subject","args":{"subject":"$car","name":"car"}},'
            '{"action":"super_fuse","args":{"subject":"$car"}},'
            '{"action":"last_seen","args":{"subject":"$car"}}],"say":"On it.","border":"nav"}\n'
            'Req: "switch to the hotel camera, turn on the heatmap and tell me how busy it is"\n'
            '=> {"steps":[{"action":"switch_camera","args":{"name":"Hotel"}},'
            '{"action":"set_module","args":{"key":"heatmap","on":true}},'
            '{"action":"camera_status","args":{}}],"say":"Here you go.","border":"nav"}\n'
            'Req: "show me the watched red trucks and how many there are"\n'
            '=> {"steps":[{"action":"filter_roster","args":{"cls":"vehicle","color":"red","subtype":'
            '"truck","watched":true}},{"action":"count_subjects","args":{"cls":"vehicle","color":"red",'
            '"subtype":"truck"}}],"say":"Filtered.","border":"nav"}\n'
            'Req: "how many people are on the plaza camera?"\n'
            '=> {"steps":[{"action":"switch_camera","args":{"name":"Plaza"}},'
            '{"action":"count","args":{"cls":"person"}}],"border":"nav"}\n\n'
            f"LIVE CONTEXT: cameras=[{cams}]; active_camera={active}; current_screen={screen}.\n"
            "REQUEST: " + command)
        plan = self._json(self.chat(
            prompt, system=("You are a planner that outputs ONLY strict JSON action-chain plans for a "
                            "surveillance UI. You break complex requests into many ordered steps and "
                            "pass data between them."),
            max_tokens=1400, temperature=0.15))
        if not isinstance(plan, dict):
            return None
        steps: list[dict] = []
        raw = plan.get("steps")
        if isinstance(raw, list):
            for s in raw:
                if isinstance(s, dict) and s.get("action"):
                    args = s.get("args")
                    step = {"action": str(s["action"]),
                            "args": args if isinstance(args, dict) else {}}
                    if s.get("as"):
                        step["as"] = str(s["as"])   # named result for data passing
                    steps.append(step)
        border = plan.get("border")
        ask = str(plan.get("ask") or "").strip() or None
        say = str(plan.get("say") or "").strip() or None
        return {"steps": steps, "say": say, "ask": ask,
                "border": border if border in ("nav", "alert") else "nav"}

    def correlate(self, alerts: list[dict]) -> dict | None:
        """Reason over recent alerts: are several one unfolding incident? (idea 5)."""
        lines = [f"- {a.get('ts','')} {a.get('severity','')} {a.get('type','')} @ "
                 f"{a.get('cam','')}: {a.get('summary','')}" for a in alerts[:30]]
        prompt = ("These are recent surveillance alerts. Decide whether several describe ONE "
                  "unfolding incident. Reply as JSON: {incident: true/false, title: short label, "
                  "assessment: one sentence, action: one recommended operator action, "
                  "cams: [camera names]}. Be factual, no identities.\n" + "\n".join(lines))
        return self._json(self.chat(prompt, system="You are a security operations analyst. Output only JSON.",
                                    max_tokens=400))

    def advise(self, alert: dict) -> str | None:
        """One concrete recommended operator action for an alert (idea 7)."""
        prompt = (f"Give ONE concrete recommended operator action (imperative, under 12 words) for this "
                  f"security alert. type={alert.get('type')}, camera={alert.get('cam')}, "
                  f"detail={alert.get('summary')}. No identities, no speculation.")
        return self.chat(prompt, system="You are a security operations SOP assistant.", max_tokens=120)

    def search_events(self, text: str, events: list[dict]) -> dict | None:
        """Semantic search over the event timeline (idea 10). Returns {answer, matches:[idx]}."""
        lines = [f"{i}: {e.get('ts','')} {e.get('type','')} @ {e.get('cam','')} {e.get('label','')}"
                 for i, e in enumerate(events[:60])]
        prompt = ("Given this timeline of events (index: time type @ camera label), answer the operator's "
                  "question. Reply as JSON: {answer: one-sentence natural answer, matches: [indices of "
                  "relevant events]}. If nothing matches, answer says so and matches is [].\nQuestion: "
                  + text + "\nEvents:\n" + "\n".join(lines))
        return self._json(self.chat(prompt, system="You search a security event log. Output only JSON.",
                                    max_tokens=500))

    def describe(self, image_bgr) -> str | None:
        """VLM scene description of a frame (kept to one line)."""
        if not self.enabled or not self.vision_model:
            return None
        try:
            import cv2
            ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
            if not ok:
                return None
            b64 = base64.b64encode(buf.tobytes()).decode()
            payload = {
                "model": self.vision_model, "max_tokens": 300, "temperature": 0.3,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": "Describe this surveillance frame in one concise sentence: "
                                             "who/what and what they're doing. No identities."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]}],
            }
            return self._post(payload, timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("VLM describe failed: %s", str(exc)[:160])
            return None

    def vqa(self, image_bgr, question: str) -> str | None:
        """Answer a free-form question about a camera frame with the vision model. The frame is the
        evidence; if the answer isn't visible, say so honestly. This is how the operator answers
        'what colour is that car', 'what is in their hand', etc. — things we don't store."""
        if not self.enabled or not self.vision_model or not (question or "").strip():
            return None
        try:
            import cv2
            ok, buf = cv2.imencode(".jpg", image_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            if not ok:
                return None
            b64 = base64.b64encode(buf.tobytes()).decode()
            payload = {
                "model": self.vision_model, "max_tokens": 320, "temperature": 0.2,
                "messages": [{"role": "user", "content": [
                    {"type": "text", "text": (
                        "You are a surveillance operator's assistant looking at ONE camera frame. Answer "
                        "the operator's question about THIS image concisely and factually. If it is not "
                        "visible or cannot be determined from the frame, say so plainly (e.g. \"can't tell "
                        "from this frame\"). No identities, no speculation beyond what is shown.\n"
                        "Question: " + question)},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]}],
            }
            return self._post(payload, timeout=60.0)
        except Exception as exc:  # noqa: BLE001
            log.warning("VLM vqa failed: %s", str(exc)[:160])
            return None
