"""Offline speech-to-text for the AI Operator, via faster-whisper (a local CTranslate2 Whisper).

Runs fully on-device (no cloud, unlike the browser's Web Speech API which fails in Electron), and
handles Turkish and English, including the odd English word inside a Turkish sentence (Whisper is
robust to code-switching). Lazy and never fatal: if faster-whisper or its model is unavailable the
feature simply reports "disabled" and the Operator stays usable by typing.

The model auto-downloads on first use (see also match/tools/export_models.py which pre-fetches it).
Size is configurable with OVERSEER_STT_MODEL (tiny/base/small/medium); "base" is a good balance.
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from loguru import logger as log

_MODEL_NAME = os.environ.get("OVERSEER_STT_MODEL", "base")
_MODELS_DIR = os.environ.get("OVERSEER_STT_DIR", "models/whisper")


class STT:
    def __init__(self) -> None:
        self._model = None
        self._failed = False
        self._lock = threading.Lock()

    def _ensure(self) -> bool:
        if self._model is not None:
            return True
        if self._failed:
            return False
        with self._lock:
            if self._model is not None:
                return True
            if self._failed:
                return False
            try:
                from faster_whisper import WhisperModel
                Path(_MODELS_DIR).mkdir(parents=True, exist_ok=True)
                log.info("loading offline STT model '{}' (one time)...", _MODEL_NAME)
                self._model = WhisperModel(_MODEL_NAME, device="cpu", compute_type="int8",
                                           download_root=_MODELS_DIR)
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("offline STT unavailable ({}): pip install faster-whisper", str(exc)[:200])
                self._failed = True
                return False

    def available(self) -> bool:
        return self._ensure()

    def transcribe(self, audio: bytes, lang: str | None = None) -> str | None:
        """Transcribe recorded audio (webm/opus/wav bytes). lang 'en'/'tr' or None to auto-detect.
        Returns the text, or None when unavailable / nothing was said."""
        if not audio or not self._ensure():
            return None
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
                f.write(audio)
                tmp = f.name
            segments, _info = self._model.transcribe(
                tmp, language=(lang or None), vad_filter=True, beam_size=1)
            return " ".join(s.text for s in segments).strip() or None
        except Exception as exc:  # noqa: BLE001
            log.warning("STT transcribe failed: {}", str(exc)[:200])
            return None
        finally:
            if tmp:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass


_shared = STT()


def transcribe(audio: bytes, lang: str | None = None) -> str | None:
    return _shared.transcribe(audio, lang)


def available() -> bool:
    return _shared.available()
