"""Correlated threat scoring: fuse co-occurring events per camera into one graded threat.

The alert engine fires each rule independently, so a real incident shows up as a scatter of
unrelated alerts (a zone breach here, a "running" there) with no sense of which combinations
actually matter. A lone "running" is a jogger; "running" together with a restricted-zone
breach in the same few seconds is an intrusion in progress. This scorer keeps a short,
per-camera memory of recent events and grades the *situation*: isolated benign signals stay
low, but dangerous combinations escalate — and a genuinely dangerous combination can raise a
threat even when no single rule was configured for it.

Deterministic and clock-free (it reads event timestamps), so it is fully unit-testable.
Worker-thread only.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

# How much each event type contributes to the standing threat score on its own. Types not
# listed contribute nothing (MOTION, PERSON, VEHICLE, SUNRISE, ... are context, not threat).
BASE_WEIGHTS: dict[str, float] = {
    "FIGHTING": 7.0,        # a brawl is a high threat on its own
    "FALLING": 3.0,
    "ABANDONED_OBJECT": 3.0,
    "RESTRICTED": 3.0,
    "LINE_CROSS": 2.0,
    "RUNNING": 2.0,
    "CROWDING": 2.0,
    "WRONG_DIRECTION": 2.0,
    "TAILGATING": 2.0,
    "REMOVED_OBJECT": 2.0,
    "OBSTRUCTION": 2.0,     # camera tampering
    "DEFOCUS": 1.5,         # camera tampering
    "CAMERA_MOVED": 2.0,    # camera tampering
    "LOITERING": 1.0,
    "U_TURN": 1.0,
    "STOPPED": 0.5,
}

# Combinations that mean far more together than apart. When every type in the set has fired
# within the window, add the bonus and record the reason. Tuned so a single benign signal
# never trips a combo — precision over recall, to keep false threats down.
COMBOS: list[tuple[frozenset[str], float, str]] = [
    (frozenset({"RESTRICTED", "RUNNING"}), 4.0, "restricted-zone intrusion at speed"),
    (frozenset({"FIGHTING", "CROWDING"}), 4.0, "violence within a crowd"),
    (frozenset({"FIGHTING", "RUNNING"}), 3.0, "altercation with people running"),
    (frozenset({"ABANDONED_OBJECT", "CROWDING"}), 3.0, "unattended item in a crowd"),
    (frozenset({"FALLING", "CROWDING"}), 2.5, "person down in a crowd"),
    (frozenset({"LOITERING", "LINE_CROSS"}), 2.0, "loitering then crossing — possible casing"),
    (frozenset({"WRONG_DIRECTION", "TAILGATING"}), 2.0, "forced/tailgated entry against flow"),
    (frozenset({"OBSTRUCTION", "DEFOCUS"}), 3.0, "camera tampering"),
    (frozenset({"OBSTRUCTION", "CAMERA_MOVED"}), 3.0, "camera tampering"),
]

# Score → level thresholds (lower bound inclusive).
_LEVELS: list[tuple[float, str]] = [
    (11.0, "critical"),
    (7.0, "high"),
    (4.0, "elevated"),
    (2.0, "low"),
    (0.0, "none"),
]

_LEVEL_SEVERITY: dict[str, str] = {
    "none": "info", "low": "info", "elevated": "warning",
    "high": "critical", "critical": "critical",
}
_SEV_RANK = {"info": 0, "warning": 1, "critical": 2}


def level_severity(level: str) -> str:
    return _LEVEL_SEVERITY.get(level, "info")


def escalate(base: str, level: str) -> str:
    """The stronger of a rule's own severity and the standing threat level's severity."""
    other = level_severity(level)
    return base if _SEV_RANK.get(base, 0) >= _SEV_RANK.get(other, 0) else other


@dataclass
class ThreatAssessment:
    level: str                       # none / low / elevated / high / critical
    score: float
    reasons: list[str] = field(default_factory=list)   # combo explanations, strongest first
    signals: list[str] = field(default_factory=list)   # event types currently in the window
    combo: bool = False              # a multi-signal combination is active (not a lone event)

    @property
    def severity(self) -> str:
        return level_severity(self.level)


@dataclass
class _Window:
    events: deque = field(default_factory=deque)   # (event_type_name, timestamp)


def _level_for(score: float) -> str:
    for lo, name in _LEVELS:
        if score >= lo:
            return name
    return "none"


class ThreatScorer:
    def __init__(self, window_s: float = 25.0, buffer: int = 64) -> None:
        self._window = float(window_s)
        self._buffer = int(buffer)
        self._by_source: dict[object, _Window] = {}

    def reset(self) -> None:
        self._by_source.clear()

    def observe(self, event) -> ThreatAssessment:
        """Record one event under its camera and return the camera's current threat."""
        name = getattr(getattr(event, "type", None), "name", None) or ""
        src = getattr(event, "source_id", None)
        ts = float(getattr(event, "timestamp", 0.0))
        win = self._by_source.get(src)
        if win is None:
            win = _Window(events=deque(maxlen=self._buffer))
            self._by_source[src] = win
        win.events.append((name, ts))
        self._trim(win, ts)
        return self._assess(win)

    def assess(self, source_id: object, now: float) -> ThreatAssessment:
        """The standing threat for a camera at time `now`, without recording a new event."""
        win = self._by_source.get(source_id)
        if win is None:
            return ThreatAssessment("none", 0.0)
        self._trim(win, now)
        return self._assess(win)

    def _trim(self, win: _Window, now: float) -> None:
        cutoff = now - self._window
        while win.events and win.events[0][1] < cutoff:
            win.events.popleft()

    def _assess(self, win: _Window) -> ThreatAssessment:
        present = {name for name, _ in win.events}
        if not present:
            return ThreatAssessment("none", 0.0)
        # base score: the strongest single signal plus a fraction of the rest, so a burst of
        # distinct threats reads higher than one repeated event without runaway stacking.
        contributing = sorted((BASE_WEIGHTS.get(n, 0.0) for n in present), reverse=True)
        contributing = [w for w in contributing if w > 0.0]
        score = 0.0
        if contributing:
            score = contributing[0] + 0.5 * sum(contributing[1:])
        reasons: list[tuple[float, str]] = []
        for members, bonus, why in COMBOS:
            if members <= present:
                score += bonus
                reasons.append((bonus, why))
        reasons.sort(reverse=True)
        level = _level_for(score)
        signals = [n for n in present if BASE_WEIGHTS.get(n, 0.0) > 0.0]
        return ThreatAssessment(
            level=level, score=round(score, 2),
            reasons=[why for _, why in reasons], signals=sorted(signals),
            combo=bool(reasons),
        )
