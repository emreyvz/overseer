"""ONVIF camera auto-discovery (WS-Discovery) + best-effort RTSP URL resolution.

`discover()` sends a WS-Discovery multicast probe and collects the ONVIF cameras that answer
on the LAN — no credentials needed just to find them. `stream_uri()` then does the ONVIF
Media handshake (GetCapabilities → GetProfiles → GetStreamUri) with the operator's login to
turn a discovered device into a ready-to-add rtsp:// URL. Both are best-effort and fail
soft: a camera that doesn't answer, or wrong credentials, just yields nothing rather than
raising, so discovery never breaks the app.
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import socket
import time
from urllib.parse import unquote, urlparse

_WSD_ADDR = "239.255.255.250"
_WSD_PORT = 3702

_PROBE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"'
    ' xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"'
    ' xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"'
    ' xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
    "<e:Header>"
    "<w:MessageID>uuid:{msgid}</w:MessageID>"
    '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
    '<w:Action e:mustUnderstand="true">'
    "http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>"
    "</e:Header>"
    "<e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>"
    "</e:Envelope>"
)


def _scope(scopes: str, key: str) -> str | None:
    m = re.search(rf"onvif://www\.onvif\.org/{key}/([^\s<]+)", scopes)
    return unquote(m.group(1)) if m else None


def parse_probe_match(text: str) -> dict | None:
    """Pull the device's service address and friendly scopes out of a ProbeMatch reply.
    Regex, not ElementTree, because WS-Discovery namespace prefixes vary wildly by vendor."""
    xa = re.search(r"XAddrs[^>]*>([^<]+)<", text)
    xaddr = xa.group(1).strip().split()[0] if xa else None
    sc = re.search(r"Scopes[^>]*>([^<]*)<", text)
    scopes = sc.group(1) if sc else ""
    if not xaddr and not scopes:
        return None
    return {
        "xaddr": xaddr,
        "name": _scope(scopes, "name"),
        "hardware": _scope(scopes, "hardware"),
        "location": _scope(scopes, "location"),
    }


def discover(timeout: float = 3.0) -> list[dict]:
    """Return the ONVIF cameras that answer a WS-Discovery probe on the local network."""
    msg = _PROBE.format(msgid=os.urandom(8).hex())
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(timeout)
        sock.sendto(msg.encode("utf-8"), (_WSD_ADDR, _WSD_PORT))
    except Exception:  # noqa: BLE001 - no network / blocked multicast -> nothing found
        return []
    found: dict[str, dict] = {}
    end = time.time() + timeout
    try:
        while time.time() < end:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                break
            except Exception:  # noqa: BLE001
                break
            dev = parse_probe_match(data.decode("utf-8", "ignore"))
            if dev is None:
                continue
            host = None
            if dev.get("xaddr"):
                host = urlparse(dev["xaddr"]).hostname
            dev["ip"] = host or addr[0]
            found[dev.get("xaddr") or dev["ip"]] = dev
    finally:
        sock.close()
    return list(found.values())


def _wsse_header(user: str, password: str) -> str:
    from datetime import datetime, timezone
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    nonce = os.urandom(16)
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + password.encode()).digest()).decode()
    ns = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
    tp = ("http://docs.oasis-open.org/wss/2004/01/"
          "oasis-200401-wss-username-token-profile-1.0#PasswordDigest")
    return (
        f'<s:Header><Security s:mustUnderstand="1" xmlns:s="{ns}">'
        f"<UsernameToken><Username>{user}</Username>"
        f'<Password Type="{tp}">{digest}</Password>'
        f'<Nonce>{base64.b64encode(nonce).decode()}</Nonce>'
        f"<Created>{created}</Created></UsernameToken></Security></s:Header>"
    )


def _soap(url: str, action: str, body: str, user: str, password: str,
          timeout: float = 5.0) -> str | None:
    import requests
    header = _wsse_header(user, password) if user else "<s:Header/>"
    envelope = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"{header}<s:Body>{body}</s:Body></s:Envelope>"
    )
    try:
        r = requests.post(
            url, data=envelope.encode("utf-8"), timeout=timeout,
            headers={"Content-Type": f'application/soap+xml; charset=utf-8; action="{action}"'},
        )
        return r.text if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def stream_uri(device_xaddr: str, user: str = "", password: str = "") -> str | None:
    """Resolve a discovered device to an rtsp:// URL via the ONVIF Media service. Best-effort;
    returns None if the camera doesn't cooperate (then the operator types the URL by hand)."""
    media_ns = "http://www.onvif.org/ver10/media/wsdl"
    caps = _soap(
        device_xaddr, "http://www.onvif.org/ver10/device/wsdl/GetCapabilities",
        '<GetCapabilities xmlns="http://www.onvif.org/ver10/device/wsdl">'
        "<Category>Media</Category></GetCapabilities>", user, password)
    media_url = None
    if caps:
        m = re.search(r"<[^>]*Media[^>]*>.*?XAddr[^>]*>([^<]+)<", caps, re.DOTALL)
        if m:
            media_url = m.group(1).strip()
    media_url = media_url or device_xaddr.replace("device_service", "media_service")
    profiles = _soap(media_url, f"{media_ns}/GetProfiles",
                     f'<GetProfiles xmlns="{media_ns}"/>', user, password)
    if not profiles:
        return None
    tok = re.search(r'Profiles[^>]*token="([^"]+)"', profiles)
    if not tok:
        return None
    su = _soap(
        media_url, f"{media_ns}/GetStreamUri",
        f'<GetStreamUri xmlns="{media_ns}"><StreamSetup>'
        '<Stream xmlns="http://www.onvif.org/ver10/schema">RTP-Unicast</Stream>'
        '<Transport xmlns="http://www.onvif.org/ver10/schema"><Protocol>RTSP</Protocol>'
        f"</Transport></StreamSetup><ProfileToken>{tok.group(1)}</ProfileToken></GetStreamUri>",
        user, password)
    if not su:
        return None
    uri = re.search(r"<[^>]*Uri[^>]*>([^<]+)<", su)
    if not uri:
        return None
    rtsp = uri.group(1).strip()
    if user and password and rtsp.startswith("rtsp://") and "@" not in rtsp:
        rtsp = rtsp.replace("rtsp://", f"rtsp://{user}:{password}@", 1)  # embed creds for the reader
    return rtsp
