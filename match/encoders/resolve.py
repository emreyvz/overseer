"""Pick the best available encoder, else a guaranteed fallback. This is where graceful
degradation is decided: a specialized ReID model is used when its weights are present,
otherwise the always-available baseline takes over and the choice is reported."""
from __future__ import annotations

from match.encoders.base import Encoder


def best_available(candidates: list[Encoder], fallback: Encoder) -> tuple[Encoder, bool]:
    """Return (encoder, used_fallback). The first candidate whose ``available()`` is True
    wins; if none are available, ``fallback`` is returned with used_fallback=True."""
    for enc in candidates:
        try:
            if enc.available():
                return (enc, False)
        except Exception:  # noqa: BLE001 - a broken adapter is just "unavailable"
            continue
    return (fallback, True)
