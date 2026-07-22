"""Evaluate events against alert rules. Thread-safe (UI edits rules, worker evaluates)."""
from __future__ import annotations

import threading

from alerts.model import Alert, AlertRule, severity_rank
from alerts.summary import EventSummarizer, SourceNameFn
from events.types import Event


class AlertEngine:
    def __init__(self, summarizer: EventSummarizer, source_name_fn: SourceNameFn) -> None:
        self._summarizer = summarizer
        self._source_name_fn = source_name_fn
        self._lock = threading.RLock()
        self._rules: list[AlertRule] = []
        self._last_fired: dict[int, float] = {}

    def set_rules(self, rules: list[AlertRule]) -> None:
        with self._lock:
            self._rules = list(rules)
            live = {r.id for r in rules}
            self._last_fired = {rid: ts for rid, ts in self._last_fired.items()
                                if rid in live}

    def reset(self) -> None:
        with self._lock:
            self._last_fired.clear()

    def evaluate(self, event: Event) -> Alert | None:
        with self._lock:
            candidates = [r for r in self._rules
                          if r.enabled and r.event_type == event.type.name
                          and self._matches(r, event)]
            if not candidates:
                return None
            rule = max(candidates, key=lambda r: severity_rank(r.severity))
            last = self._last_fired.get(rule.id)
            if last is not None and event.timestamp - last < rule.cooldown_s:
                return None
            self._last_fired[rule.id] = event.timestamp
            meta = dict(event.metadata or {})
            snap = meta.get("snapshot_path")
            return Alert(
                rule_id=rule.id, rule_name=rule.name, event_type=event.type.name,
                source_id=event.source_id, severity=rule.severity,
                summary=self._summarizer.summarize_event(event, self._source_name_fn),
                timestamp=event.timestamp,
                snapshot_path=str(snap) if snap else None, metadata=meta,
            )

    @staticmethod
    def _matches(rule: AlertRule, event: Event) -> bool:
        meta = event.metadata or {}
        if rule.source_id is not None and rule.source_id != event.source_id:
            return False
        if rule.zone_id is not None and rule.zone_id != meta.get("zone_id"):
            return False
        if rule.min_count is not None and int(meta.get("count") or 0) < rule.min_count:
            return False
        if (rule.min_duration_s is not None
                and float(meta.get("duration") or 0) < rule.min_duration_s):
            return False
        if rule.min_confidence is not None and (event.confidence or 0) < rule.min_confidence:
            return False
        return True
