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

# Coarse foot-point density grid used to auto-suggest WHERE a watch zone belongs (the busiest
# region of a camera's scene). Kept tiny so it is essentially free per frame.
_GW, _GH = 16, 12
_ZONE_MIN_SAMPLES = 40      # need this many foot-points before proposing a zone


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
                 "person": 0, "vehicle": 0, "conf": 0.0, "crowd": 0.0, "reconnects": 0,
                 "grid": None, "grid_n": 0}
            self._p[sid] = p
        return p

    def observe_frame(self, sid: object, *, brightness: float, motion: float, fps: float,
                      dets: list, points: list | None = None) -> None:
        """dets: list of (cls, confidence) for this frame. points: optional normalized (x,y)
        foot-points (bottom-centre of each person/vehicle box) used to build the density grid
        that drives auto zone suggestions."""
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
            for x, y in (points or ()):
                if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
                    if p["grid"] is None:
                        p["grid"] = [0.0] * (_GW * _GH)
                    gx = min(_GW - 1, int(x * _GW))
                    gy = min(_GH - 1, int(y * _GH))
                    p["grid"][gy * _GW + gx] += 1.0
                    p["grid_n"] += 1

    def suggested_zone(self, sid: object) -> dict | None:
        """Propose WHERE to put a watch zone on this camera: a normalized rectangle polygon around
        the busiest region of the accumulated foot-point density, plus the fraction of traffic it
        covers (for the explanation). Returns None until enough activity has been seen."""
        with self._lock:
            p = self._p.get(sid)
            if not p or not p.get("grid") or p.get("grid_n", 0) < _ZONE_MIN_SAMPLES:
                return None
            grid = list(p["grid"])
            n = p["grid_n"]
        mx = max(grid)
        if mx <= 0:
            return None
        thr = 0.45 * mx                                  # cells within ~half the peak are "hot"
        hot = [(i % _GW, i // _GW) for i, v in enumerate(grid) if v >= thr]
        if not hot:
            return None
        xs = [c[0] for c in hot]
        ys = [c[1] for c in hot]
        x0, x1 = min(xs) / _GW, (max(xs) + 1) / _GW
        y0, y1 = min(ys) / _GH, (max(ys) + 1) / _GH
        pad_x, pad_y = 0.5 / _GW, 0.5 / _GH              # a touch of breathing room
        x0 = max(0.0, x0 - pad_x); x1 = min(1.0, x1 + pad_x)
        y0 = max(0.0, y0 - pad_y); y1 = min(1.0, y1 + pad_y)
        inside = sum(v for i, v in enumerate(grid)
                     if x0 <= (i % _GW + 0.5) / _GW <= x1 and y0 <= (i // _GW + 0.5) / _GH <= y1)
        return {"polygon": [[round(x0, 3), round(y0, 3)], [round(x1, 3), round(y0, 3)],
                            [round(x1, 3), round(y1, 3)], [round(x0, 3), round(y1, 3)]],
                "coverage": round(inside / n, 2), "samples": int(n)}

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
