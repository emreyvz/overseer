"""Event model shared across the application."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class EventType(Enum):
    MOTION = auto()
    PERSON = auto()
    VEHICLE = auto()
    ANIMAL = auto()
    ENVIRONMENT = auto()
    CAMERA_HEALTH = auto()
    SYSTEM = auto()
    LIGHTNING = auto()
    METEOR = auto()
    SATELLITE = auto()
    RESTRICTED = auto()
    LOITERING = auto()
    LINE_CROSS = auto()
    RUNNING = auto()
    STOPPED = auto()
    U_TURN = auto()
    WRONG_DIRECTION = auto()
    TAILGATING = auto()
    QUEUE = auto()
    FALLING = auto()
    CROWDING = auto()
    FIGHTING = auto()
    ABANDONED_OBJECT = auto()
    REMOVED_OBJECT = auto()
    DEFOCUS = auto()
    OBSTRUCTION = auto()
    CAMERA_MOVED = auto()
    ANOMALY = auto()
    SUNRISE = auto()
    SUNSET = auto()


# Single source of truth for the Turkish display label of each event type
# name, shared by the timeline, search dialog, statistics dialog and PDF
# report (previously each kept its own copy of this mapping).
EVENT_TYPE_LABELS: list[tuple[str, str]] = [
    ("MOTION", "Motion"), ("PERSON", "Person"), ("VEHICLE", "Vehicle"),
    ("ANIMAL", "Animal"), ("ENVIRONMENT", "Environment"), ("CAMERA_HEALTH", "Camera"),
    ("SYSTEM", "System"),
    ("LIGHTNING", "Lightning"),
    ("METEOR", "Meteor"),
    ("SATELLITE", "Satellite"),
    ("RESTRICTED", "Restricted Zone"),
    ("LOITERING", "Loitering"),
    ("LINE_CROSS", "Line Cross"),
    ("RUNNING", "Running"),
    ("STOPPED", "Stopped"),
    ("U_TURN", "U-Turn"),
    ("WRONG_DIRECTION", "Wrong Direction"),
    ("TAILGATING", "Tailgating"),
    ("QUEUE", "Queue"),
    ("FALLING", "Falling"),
    ("CROWDING", "Crowding"),
    ("FIGHTING", "Fighting"),
    ("ABANDONED_OBJECT", "Abandoned Object"),
    ("REMOVED_OBJECT", "Removed Object"),
    ("DEFOCUS", "Defocus"),
    ("OBSTRUCTION", "Obstruction"),
    ("CAMERA_MOVED", "Camera Moved"),
    ("ANOMALY", "Anomaly"),
    ("SUNRISE", "Sunrise"),
    ("SUNSET", "Sunset"),
]
TYPE_NAMES: dict[str, str] = {name: label for name, label in EVENT_TYPE_LABELS}


@dataclass(frozen=True)
class Event:
    type: EventType
    timestamp: float
    source_id: int | None
    label: str
    confidence: float | None = None
    bbox: tuple[int, int, int, int] | None = None
    metadata: dict[str, object] = field(default_factory=dict)
