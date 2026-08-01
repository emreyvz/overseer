"""Offline text-to-speech for the AI Operator's spoken replies, via pyttsx3 (the OS speech engine:
SAPI5 on Windows, NSSpeechSynthesizer on macOS, espeak on Linux). This is a reliable fallback for
when the browser's speechSynthesis has no voices, which is common in Electron. Lazy and never fatal:
if pyttsx3 or an OS voice is unavailable it reports "disabled" and the frontend falls back to the
browser voice (or stays silent).
"""
from __future__ import annotations

import os
import tempfile
import threading

from loguru import logger as log

_lock = threading.Lock()      # pyttsx3 is not thread-safe
_failed = False


def available() -> bool:
    global _failed
    if _failed:
        return False
    try:
        import pyttsx3  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        _failed = True
        return False


def _pick_voice(engine, lang: str | None) -> None:
    """Prefer a voice matching the requested language (tr/en)."""
    want = "tr" if (lang or "").lower().startswith("tr") else "en"
    try:
        for v in engine.getProperty("voices"):
            hay = " ".join(str(x) for x in (getattr(v, "languages", []) or []))
            hay = f"{hay} {getattr(v, 'id', '')} {getattr(v, 'name', '')}".lower()
            if want in hay:
                engine.setProperty("voice", v.id)
                return
    except Exception:  # noqa: BLE001
        pass


def synth(text: str, lang: str | None = None) -> bytes | None:
    """Synthesize `text` to WAV bytes (or None when unavailable)."""
    global _failed
    if not text or _failed:
        return None
    tmp = None
    try:
        import pyttsx3
        with _lock:
            engine = pyttsx3.init()
            _pick_voice(engine, lang)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp = f.name
            engine.save_to_file(text, tmp)
            engine.runAndWait()
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass
        with open(tmp, "rb") as f:
            data = f.read()
        return data or None
    except Exception as exc:  # noqa: BLE001
        log.warning("TTS synth failed: {}", str(exc)[:200])
        _failed = True
        return None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
