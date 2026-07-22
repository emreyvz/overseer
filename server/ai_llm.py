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
    "RUNNING", "STOPPED", "WRONG_DIRECTION", "TAILGATING", "QUEUE", "FALLING",
    "CROWDING", "FIGHTING", "ABANDONED_OBJECT", "REMOVED_OBJECT",
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
}


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

    def chat(self, prompt: str, system: str | None = None, max_tokens: int = 700) -> str | None:
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return self._post({"model": self.model, "messages": msgs, "max_tokens": max_tokens,
                           "temperature": 0.4, "thinking": {"type": "disabled"}})

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
