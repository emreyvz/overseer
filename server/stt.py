"""Offline speech-to-text for the AI Operator, via faster-whisper (a local CTranslate2 Whisper).

Runs fully on-device (no cloud, unlike the browser's Web Speech API which fails in Electron), and
handles Turkish and English, including the odd English word inside a Turkish sentence (Whisper is
robust to code-switching). Lazy and never fatal: if faster-whisper or its model is unavailable the
feature simply reports "disabled" and the Operator stays usable by typing.

The model auto-downloads on first use (see also match/tools/export_models.py which pre-fetches it).
Size is configurable with OVERSEER_STT_MODEL (tiny/base/small/medium). Default is "small": "base"
mis-transcribes Turkish badly, and "small" is far more accurate there while still running in well
under a second per command on a GPU (float16) and acceptably on CPU (int8).
"""
from __future__ import annotations

import os
import tempfile
import threading
from pathlib import Path

from loguru import logger as log

# large-v3-turbo: near large-v3 accuracy (a big jump over 'small' for Turkish AND English) but
# ~2-4x faster with far lower VRAM, so it fits alongside the vision models on the GPU. Downloaded
# on first use into _MODELS_DIR (or pre-fetched by match.tools.export_models --only whisper).
_MODEL_NAME = os.environ.get("OVERSEER_STT_MODEL", "large-v3-turbo")
_MODELS_DIR = os.environ.get("OVERSEER_STT_DIR", "models/whisper")


class STT:
    def __init__(self) -> None:
        self._model = None
        self._failed = False
        self._lock = threading.Lock()
        self._device = "cpu"
        self._beam = 1

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
                # Use the GPU when present (much faster); fall back to int8 on CPU.
                device, compute = "cpu", "int8"
                try:
                    import torch
                    if torch.cuda.is_available():
                        # int8_float16 (not plain float16): halves the large model's VRAM so it
                        # coexists with depth+detector+CLIP+ReID without OOMing, at ~the same accuracy.
                        device, compute = "cuda", "int8_float16"
                except Exception:  # noqa: BLE001
                    pass
                log.info("loading offline STT model '{}' on {} (one time)...", _MODEL_NAME, device)
                try:
                    self._model = WhisperModel(_MODEL_NAME, device=device, compute_type=compute,
                                               download_root=_MODELS_DIR)
                except Exception as gpu_exc:  # noqa: BLE001
                    # The GPU can be full (depth + detector + CLIP + ReID already resident), so a cuda
                    # load OOMs. Voice must not die for that: fall back to CPU, which is plenty for a
                    # short command. Without this the operator silently transcribes nothing.
                    if device != "cuda":
                        raise
                    log.warning("STT cuda load failed ({}); falling back to CPU", str(gpu_exc)[:120])
                    device, compute = "cpu", "int8"
                    self._model = WhisperModel(_MODEL_NAME, device=device, compute_type=compute,
                                               download_root=_MODELS_DIR)
                self._device = device
                # A GPU makes a wider beam nearly free, and it sharpens Turkish noticeably; on CPU
                # stay greedy so a command still returns quickly.
                self._beam = 5 if device == "cuda" else 1
                return True
            except Exception as exc:  # noqa: BLE001
                log.warning("offline STT unavailable ({}): pip install faster-whisper", str(exc)[:200])
                self._failed = True
                return False

    def available(self) -> bool:
        return self._ensure()

    @staticmethod
    def _wav_to_array(audio: bytes):
        """Decode a 16-bit PCM WAV (what the client sends, already 16 kHz mono) into a float32
        numpy array — so we never depend on ffmpeg/av to decode webm. Returns None if not a WAV."""
        if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
            return None
        try:
            import io
            import wave
            import numpy as np
            with wave.open(io.BytesIO(audio), "rb") as w:
                ch, sw = w.getnchannels(), w.getsampwidth()
                raw = w.readframes(w.getnframes())
            if sw != 2:
                return None
            arr = np.frombuffer(raw, dtype="<i2").astype("float32") / 32768.0
            if ch > 1:
                arr = arr.reshape(-1, ch).mean(axis=1)
            return arr
        except Exception:  # noqa: BLE001
            return None

    def transcribe(self, audio: bytes, lang: str | None = None) -> str | None:
        """Transcribe recorded audio. The client sends 16 kHz mono WAV (decoded here without
        ffmpeg); a webm/opus fallback is decoded by faster-whisper. lang 'en'/'tr' or auto.
        Returns the text, or None when unavailable / nothing was said."""
        if not audio or not self._ensure():
            return None
        tmp = None
        try:
            arr = self._wav_to_array(audio)
            src = arr
            if arr is None:
                with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
                    f.write(audio)
                    tmp = f.name
                src = tmp

            def _run(vad: bool) -> str:
                # No cross-segment context, no timestamps; language honoured ('tr' for Turkish).
                opts = dict(language=(lang or None), vad_filter=vad, beam_size=self._beam,
                            condition_on_previous_text=False, without_timestamps=True)
                segments, _info = self._model.transcribe(src, **opts)
                return " ".join(s.text for s in segments).strip()

            text = _run(True)
            if not text:   # a short / quiet command can be gated away entirely by VAD -> retry raw
                text = _run(False)
            return text or None
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
