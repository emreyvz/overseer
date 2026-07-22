"""PTZ control (feature 13) — best-effort ONVIF ContinuousMove.

Real movement needs a PTZ-capable camera reachable over ONVIF with credentials.
Without onvif-zeep (or on any failure) calls are logged no-ops so the UI degrades
gracefully instead of erroring. WebRTC low-latency transport is documented in
FEATURES.md — it needs a media server (aiortc/MediaMTX) and is out of scope here."""
from __future__ import annotations

import logging

log = logging.getLogger("overseer.ptz")


class PTZController:
    def __init__(self) -> None:
        self._cams: dict[str, object] = {}
        self._available: bool | None = None

    def _probe(self) -> bool:
        if self._available is None:
            try:
                import onvif  # noqa: F401
                self._available = True
            except Exception:  # noqa: BLE001
                self._available = False
                log.info("onvif-zeep not installed — PTZ runs as no-op")
        return self._available

    def _cam(self, host: str, port: int, user: str, pwd: str):
        key = f"{host}:{port}"
        if key not in self._cams:
            from onvif import ONVIFCamera
            cam = ONVIFCamera(host, port, user, pwd)
            media = cam.create_media_service()
            ptz = cam.create_ptz_service()
            token = media.GetProfiles()[0].token
            self._cams[key] = (ptz, token)
        return self._cams[key]

    def move(self, host: str, pan: float, tilt: float, zoom: float,
             *, port: int = 80, user: str = "admin", pwd: str = "") -> dict:
        """Continuous move; pan/tilt/zoom in [-1,1]. Best-effort."""
        if not self._probe():
            return {"ok": False, "reason": "onvif-unavailable"}
        try:
            ptz, token = self._cam(host, port, user, pwd)
            if pan == 0 and tilt == 0 and zoom == 0:
                ptz.Stop({"ProfileToken": token})
            else:
                req = ptz.create_type("ContinuousMove")
                req.ProfileToken = token
                req.Velocity = {"PanTilt": {"x": pan, "y": tilt}, "Zoom": {"x": zoom}}
                ptz.ContinuousMove(req)
            return {"ok": True}
        except Exception as exc:  # noqa: BLE001
            log.warning("PTZ move failed for %s: %s", host, exc)
            return {"ok": False, "reason": str(exc)}
