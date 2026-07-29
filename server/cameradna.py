"""Camera DNA + reputation: each camera develops a behavioural profile and a reliability
score from what it actually observes, instead of being treated identically.

The analysis pipeline feeds per-frame signals here — brightness, motion, fps, and the class
and confidence of each detection — plus connection events. From the running aggregates the
profile derives DNA tags (pedestrian- vs vehicle-heavy, crowded vs quiet, night-dominant,
high-motion, low-light) and a 0..1 reputation blended from detection quality, frame-rate
adequacy, lighting and connection stability. Advisory: surfaced to the operator and available
to weight decisions, never silently overriding them.

Thread-safe; session-scoped and in-memory.
"""
from __future__ import annotations

import threading

_NIGHT_BRIGHTNESS = 55.0    # mean frame brightness below this counts as "night"


def _ema(prev: float, x: float, a: float = 0.03) -> float:
    return x if prev == 0.0 else a * x + (1 - a) * prev


class CameraProfiles:
    def __init__(self) -> None:
        self._p: dict[object, dict] = {}
        self._lock = threading.Lock()

    def _get(self, sid: object) -> dict:
        p = self._p.get(sid)
        if p is None:
            p = {"frames": 0, "brightness": 0.0, "motion": 0.0, "fps": 0.0, "night": 0,
                 "person": 0, "vehicle": 0, "conf": 0.0, "crowd": 0.0, "reconnects": 0}
            self._p[sid] = p
        return p

    def observe_frame(self, sid: object, *, brightness: float, motion: float, fps: float,
                      dets: list) -> None:
        """dets: list of (cls, confidence) for this frame."""
        with self._lock:
            p = self._get(sid)
            p["frames"] += 1
            p["brightness"] = _ema(p["brightness"], float(brightness))
            p["motion"] = _ema(p["motion"], float(motion))
            p["fps"] = _ema(p["fps"], float(fps))
            if brightness < _NIGHT_BRIGHTNESS:
                p["night"] += 1
            persons = sum(1 for c, _ in dets if c == "person")
            vehicles = sum(1 for c, _ in dets if c == "vehicle")
            p["person"] += persons
            p["vehicle"] += vehicles
            p["crowd"] = _ema(p["crowd"], float(persons), a=0.05)
            for _, conf in dets:
                p["conf"] = _ema(p["conf"], float(conf), a=0.02)

    def note_reconnect(self, sid: object) -> None:
        with self._lock:
            self._get(sid)["reconnects"] += 1

    @staticmethod
    def _dna(p: dict) -> list[str]:
        tags: list[str] = []
        total = p["person"] + p["vehicle"]
        if total >= 15:
            if p["vehicle"] > p["person"] * 1.5:
                tags.append("vehicle heavy")
            elif p["person"] > p["vehicle"] * 1.5:
                tags.append("pedestrian heavy")
        if p["frames"] >= 20 and p["night"] / p["frames"] > 0.5:
            tags.append("night dominant")
        if p["brightness"] and p["brightness"] < 45:
            tags.append("low light")
        if p["crowd"] > 5:
            tags.append("crowded")
        elif total < 4 and p["frames"] >= 30:
            tags.append("quiet")
        if p["motion"] > 7:
            tags.append("high motion")
        elif p["motion"] and p["motion"] < 1.5:
            tags.append("static scene")
        return tags

    @staticmethod
    def _reputation(p: dict) -> float:
        conf = min(1.0, p["conf"])                                  # detection quality
        fps_ok = min(1.0, p["fps"] / 12.0)                          # frame-rate adequacy
        light = max(0.0, 1.0 - abs(p["brightness"] - 128.0) / 128.0)  # mid-brightness closeness
        stability = max(0.0, 1.0 - p["reconnects"] / 10.0)         # connection stability
        return round(0.4 * conf + 0.2 * fps_ok + 0.2 * light + 0.2 * stability, 2)

    def profile(self, sid: object, name: str | None = None) -> dict:
        with self._lock:
            p = self._p.get(sid)
            if p is None:
                return {"id": sid, "name": name, "dna": [], "reputation": 0.0, "frames": 0}
            return {"id": sid, "name": name, "dna": self._dna(p),
                    "reputation": self._reputation(p), "frames": p["frames"],
                    "brightness": round(p["brightness"], 1), "motion": round(p["motion"], 1),
                    "fps": round(p["fps"], 1), "reconnects": p["reconnects"],
                    "person": p["person"], "vehicle": p["vehicle"]}

    def all(self, names: dict | None = None) -> list[dict]:
        names = names or {}
        with self._lock:
            ids = list(self._p)
        return [self.profile(sid, names.get(sid)) for sid in ids]

    def reset(self) -> None:
        with self._lock:
            self._p.clear()
