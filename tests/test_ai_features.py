"""LLM feature-flag gating + rule parsing (graceful-degradation contract). No network:
chat is stubbed. Proves a disabled provider or a switched-off feature is an inert no-op,
and that make_rule only ever yields a valid, safe rule spec."""
from server.ai_llm import DEFAULT_FEATURES, LLMClient


def _client(enabled=True):
    c = LLMClient()
    c.base = "http://example" if enabled else ""
    c.key = "k" if enabled else ""
    c.features = dict(DEFAULT_FEATURES)
    return c


def test_all_features_default_on():
    assert set(DEFAULT_FEATURES) >= {"chat", "search", "rules", "correlate", "advise", "semantic"}
    assert all(DEFAULT_FEATURES.values())


def test_feature_gating():
    c = _client(enabled=True)
    assert c.feature("chat") is True
    c.features["chat"] = False
    assert c.feature("chat") is False          # switched off → off
    assert c.feature("search") is True         # others unaffected


def test_disabled_provider_gates_everything():
    c = _client(enabled=False)                 # no base/key
    assert all(not c.feature(k) for k in DEFAULT_FEATURES)  # every capability inert


def test_json_extraction():
    assert LLMClient._json('{"a": 1}') == {"a": 1}
    assert LLMClient._json('sure: {"a": 1} done') == {"a": 1}   # prose-wrapped
    assert LLMClient._json("no json here") is None
    assert LLMClient._json(None) is None


def test_make_rule_validates_and_normalises():
    c = _client()
    c.chat = lambda *a, **k: '{"name":"night car","event_type":"vehicle","severity":"CRITICAL","source_id":2}'
    r = c.make_rule("alert if a vehicle enters at night")
    assert r["event_type"] == "VEHICLE"        # upper-cased
    assert r["severity"] == "critical"         # lower-cased, valid
    assert r["source_id"] == 2


def test_make_rule_rejects_bad_event_type():
    c = _client()
    c.chat = lambda *a, **k: '{"event_type":"TELEPORT","severity":"warning"}'
    assert c.make_rule("x") is None            # unknown event → no rule, caller creates nothing


def test_make_rule_defaults_bad_severity_to_warning():
    c = _client()
    c.chat = lambda *a, **k: '{"event_type":"FIGHTING","severity":"apocalyptic"}'
    r = c.make_rule("x")
    assert r["severity"] == "warning"
