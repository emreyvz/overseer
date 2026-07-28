from server.onvif import _scope, parse_probe_match

_SAMPLE = (
    "<soap:Envelope><soap:Body><d:ProbeMatches><d:ProbeMatch>"
    "<d:XAddrs>http://192.168.1.64/onvif/device_service "
    "http://[fe80::1]/onvif/device_service</d:XAddrs>"
    "<d:Scopes>onvif://www.onvif.org/name/HIKVISION%20DS-2CD "
    "onvif://www.onvif.org/hardware/DS-2CD2042 "
    "onvif://www.onvif.org/location/lobby</d:Scopes>"
    "</d:ProbeMatch></d:ProbeMatches></soap:Body></soap:Envelope>"
)


def test_parse_probe_match_fields() -> None:
    d = parse_probe_match(_SAMPLE)
    assert d is not None
    assert d["xaddr"] == "http://192.168.1.64/onvif/device_service"  # first XAddr only
    assert d["name"] == "HIKVISION DS-2CD"                            # %20 decoded
    assert d["hardware"] == "DS-2CD2042"
    assert d["location"] == "lobby"


def test_parse_probe_match_empty() -> None:
    assert parse_probe_match("<soap:Envelope/>") is None


def test_scope_missing_key() -> None:
    assert _scope("onvif://www.onvif.org/name/Cam", "hardware") is None
    assert _scope("onvif://www.onvif.org/name/Cam", "name") == "Cam"
