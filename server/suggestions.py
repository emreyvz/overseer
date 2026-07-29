"""Smart suggestions: turn raw counts and camera profiles into proactive, explainable
recommendations. Pure functions — no DB, no I/O — so the logic is unit-testable in isolation.
The Backend gathers the data (event counts, existing rules, camera profiles) and delegates here.

Two kinds are produced:
  * "alert"  — a behaviour a camera keeps seeing that has no rule yet; carries a ready-to-apply
               `rule` dict for one-click acceptance.
  * "camera" — an improvement advisory derived from the camera reputation signals (flaky link,
               poor lighting, low detection quality). Advisory only, no action attached.
Every suggestion states its evidence ("why") so an operator can judge it — nothing is invented.
"""
from __future__ import annotations

# Behaviours worth alerting on when a camera sees them repeatedly.
BEHAVIOR_EVENTS = {
    "LOITERING", "LINE_CROSS", "RESTRICTED", "RUNNING", "FIGHTING", "CROWDING",
    "ABANDONED_OBJECT", "FALLING", "WRONG_DIRECTION", "TAILGATING", "U_TURN",
}
# Behaviours severe enough that the proposed rule defaults to critical severity.
CRITICAL_EVENTS = {"FIGHTING", "FALLING", "ABANDONED_OBJECT", "WEAPON"}


def alert_suggestions(
    counts_by_source: dict[int, dict[str, int]],
    names: dict[int, str],
    existing_rules: set[tuple[str, int | None]],
    *,
    min_events: int,
    retention_days: int,
) -> list[dict]:
    """One suggestion per (camera, behaviour) that fires >= min_events times in the window and
    is covered by no existing rule (neither a per-camera rule nor a global one)."""
    out: list[dict] = []
    for sid, counts in counts_by_source.items():
        name = names.get(sid, f"CAM {sid}")
        for etype, n in counts.items():
            if etype not in BEHAVIOR_EVENTS or n < min_events:
                continue
            if (etype, sid) in existing_rules or (etype, None) in existing_rules:
                continue
            pretty = etype.replace("_", " ").lower()
            out.append({
                "kind": "alert", "cam": name, "count": int(n),
                "title": f"Alert on {pretty} at {name}",
                "why": f"{pretty} seen {int(n)}× here in the last {retention_days} days, "
                       "with no rule yet",
                "rule": {
                    "name": f"{etype} · {name}", "event_type": etype, "source_id": sid,
                    "severity": "critical" if etype in CRITICAL_EVENTS else "warning",
                },
            })
    return out


def camera_suggestions(profiles: list[dict], *, min_frames: int = 40) -> list[dict]:
    """Improvement advisories from the per-camera behavioural profiles (Camera DNA + reputation).
    Cameras with too little data (< min_frames observed) are skipped so we never scold a camera
    we barely watched."""
    out: list[dict] = []
    for p in profiles:
        if p.get("frames", 0) < min_frames:
            continue
        nm = p.get("name") or f"CAM {p.get('id')}"
        seen = p.get("person", 0) + p.get("vehicle", 0)
        if p.get("reconnects", 0) >= 3:
            out.append({"kind": "camera", "cam": nm, "title": f"Frequent disconnects at {nm}",
                        "why": f"{p['reconnects']} reconnects this session — check the network "
                               "link or power"})
        if "low light" in p.get("dna", ()):
            out.append({"kind": "camera", "cam": nm, "title": f"Poor lighting at {nm}",
                        "why": "very low brightness — add illumination or enable IR for reliable "
                               "detection"})
        if p.get("reputation", 1.0) < 0.4 and seen > 10:
            out.append({"kind": "camera", "cam": nm, "title": f"Low detection quality at {nm}",
                        "why": f"reputation {int(p['reputation'] * 100)}% — check the camera's "
                               "angle, focus or occlusion"})
    return out
