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
# Behaviours that are DEFINED by a zone (so a zone suggestion can seed a rule for them).
ZONE_BEHAVIORS = {"LOITERING", "LINE_CROSS", "RESTRICTED", "WRONG_DIRECTION", "TAILGATING"}


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


def zone_suggestions(
    zone_by_source: dict[int, dict],
    names: dict[int, str],
    existing_rules: set[tuple[str, int | None]],
    *,
    min_coverage: float = 0.22,
) -> list[dict]:
    """Proactively propose WHERE to draw a watch zone: for each camera whose foot-traffic clearly
    clusters in one area (from the density grid) and that has no zone-behaviour rule yet, suggest a
    ready-to-edit zone polygon + a loitering rule, with the reason (how much traffic it covers).
    This is the 'zone detection' step — the operator no longer has to guess where to draw."""
    out: list[dict] = []
    for sid, z in zone_by_source.items():
        if not z or not z.get("polygon"):
            continue
        cov = float(z.get("coverage", 0.0))
        if cov < min_coverage:
            continue
        if any((b, sid) in existing_rules or (b, None) in existing_rules for b in ZONE_BEHAVIORS):
            continue  # camera already has a zone-behaviour rule; don't nag
        name = names.get(sid, f"CAM {sid}")
        pct = int(round(cov * 100))
        samples = int(z.get("samples", 0))
        out.append({
            "kind": "zone", "cam": name, "count": samples,
            "title": f"Set up a watch zone at {name}",
            "why": f"{pct}% of the foot traffic here clusters in one area ({samples} tracks seen) "
                   "and no zone watches it yet — this is where a loitering/intrusion zone belongs",
            "zone": z["polygon"],
            "rule": {
                "name": f"LOITERING · {name}", "event_type": "LOITERING", "source_id": sid,
                "severity": "warning",
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


_KIND_WHY = {
    "occlusion": "an object standing in the way casts a permanent shadow across this ground",
    "resolution": "this ground is too far away to meet the chosen identification standard",
    "radiometric": "this region is too dark, too blown out or too blurred to be usable",
    "empirical": "tracks keep ending here away from the frame edge, which means the system is "
                 "losing people at this spot",
}


def coverage_suggestions(spots_by_source: dict[int, list[dict]], names: dict[int, str],
                         *, max_per_camera: int = 3) -> list[dict]:
    """FOG OF WAR blind spots as actionable work items.

    A picture of a coverage gap is interesting; a ranked gap with a named remedy and a recovered
    area is a task someone can close. Only PERSISTENT spots are surfaced — a van parked for ten
    minutes is not an estate-management problem.
    """
    out: list[dict] = []
    for sid, spots in spots_by_source.items():
        nm = names.get(sid, f"CAM {sid}")
        for s in spots[:max_per_camera]:
            area = s.get("area_m2")
            events = int(s.get("events") or 0)
            why = _KIND_WHY.get(str(s.get("kind")), "this ground cannot be observed")
            if area:
                why += f" (~{area:.0f} m2, estimated)"
            if events:
                why += f"; {events} track{'' if events == 1 else 's'} already lost here"
            remedy = (s.get("remedies") or [{}])[0].get("text")
            if remedy:
                why += f". Suggested: {remedy.lower()}"
            out.append({
                "kind": "coverage", "cam": nm,
                "title": f"Blind spot at {s.get('name', 'an unobserved area')} on {nm}",
                "why": why,
                "count": events,
                "spot": {"id": s.get("id"), "polygon": s.get("polygon"), "kind": s.get("kind")},
            })
    return out
