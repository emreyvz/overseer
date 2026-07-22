"""Alert domain model: rules and raised alerts. Pure, Qt-free."""
from __future__ import annotations

from dataclasses import dataclass, field

SEVERITY_ORDER: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}
SEVERITY_LABELS: dict[str, str] = {"info": "info", "warning": "warning", "critical": "critical"}


def severity_rank(severity: str) -> int:
    return SEVERITY_ORDER.get(severity, 0)


@dataclass(frozen=True)
class AlertRule:
    id: int
    name: str
    event_type: str            # EventType.name
    source_id: int | None      # None = all cameras
    zone_id: int | None        # None = any zone (matched via event.metadata["zone_id"])
    min_count: int | None      # None = no threshold (event.metadata["count"])
    min_duration_s: float | None   # None = no threshold (event.metadata["duration"])
    min_confidence: float | None   # None = no threshold (event.confidence)
    severity: str              # "info" | "warning" | "critical"
    cooldown_s: float          # suppress repeat firing of the same rule
    enabled: bool


@dataclass
class Alert:
    rule_id: int
    rule_name: str
    event_type: str
    source_id: int | None
    severity: str
    summary: str
    timestamp: float
    snapshot_path: str | None = None
    metadata: dict = field(default_factory=dict)
    acknowledged: bool = False
    id: int = 0                # DB row id; 0 = not yet persisted
