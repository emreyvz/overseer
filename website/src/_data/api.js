// API reference data: base/auth notes, grouped endpoints, and the error table.
module.exports = {
  base: "http://127.0.0.1:8787",
  auth:
    "Overseer runs as a local, single-operator service and ships with no authentication by " +
    "default, it binds to localhost and never calls out to the internet. For multi-user or " +
    "networked deployments, front the API with a reverse proxy that enforces auth (bearer token, " +
    "mTLS or your SSO). All examples below assume the local base URL.",
  groups: [
    {
      title: "Sources & Streams",
      endpoints: [
        { method: "GET", path: "/api/sources", desc: "List registered camera / video sources." },
        { method: "POST", path: "/api/sources", desc: "Register a source.", body: '{ "name": "North Gate", "url": "rtsp://…" }' },
        { method: "POST", path: "/api/discover", desc: "Discover ONVIF cameras on the LAN.", body: '{ "timeout": 3.0 }' },
        { method: "GET", path: "/stream/{source_id}", desc: "MJPEG stream for a source (live feed)." },
        { method: "GET", path: "/snap/{source_id}", desc: "Latest JPEG snapshot for a source." },
      ],
    },
    {
      title: "Detection & Filters",
      endpoints: [
        { method: "GET", path: "/api/detection/filters", desc: "Current per-class DETECTION toggles (person / vehicle / animal / weapon / motion / track)." },
        { method: "POST", path: "/api/detection/filters", desc: "Update toggles; persisted and applied live.", body: '{ "vehicle": false, "motion": false }' },
      ],
    },
    {
      title: "Spatial & Reconstruction",
      endpoints: [
        { method: "GET", path: "/api/spatial/{source_id}?grid=320", desc: "Lift a frame into a 3D scene: depth grid, point cloud entities, FOV, background layer." },
        { method: "GET", path: "/api/subjects/{id}/reconstruct", desc: "Super-resolved reconstruction of a subject from its sightings." },
        { method: "GET", path: "/api/reconstruct/plate/{det_id}", desc: "Reconstruct a clearer licence plate for a detection." },
      ],
    },
    {
      title: "Identity & Roster",
      endpoints: [
        { method: "GET", path: "/api/roster", desc: "List roster entries (tracked subjects)." },
        { method: "GET", path: "/api/roster/{id}", desc: "A single subject with attributes, trail and flags." },
        { method: "POST", path: "/api/roster/{id}/watch", desc: "Flag / unflag a subject as watched (BOLO).", body: '{ "on": true }' },
        { method: "GET", path: "/api/subjects/{id}/dossier", desc: "Long-term dossier: sightings, histogram, biometrics." },
        { method: "GET", path: "/api/roster/{id}/graph", desc: "Ego relationship graph (who-was-with-whom)." },
      ],
    },
    {
      title: "Analytics & Alerts",
      endpoints: [
        { method: "GET", path: "/api/suggestions", desc: "Smart alert-coverage and camera-health suggestions." },
        { method: "POST", path: "/api/alerts/rules", desc: "Add an alert rule.", body: '{ "name": "Loitering", "event_type": "LOITERING", "source_id": 1, "severity": "warning" }' },
        { method: "GET", path: "/api/cameras/dna", desc: "Per-camera learned DNA / reputation signals." },
      ],
    },
    {
      title: "WebSocket",
      endpoints: [
        { method: "WS", path: "/ws", desc: "Live stream: frame meta, detections, metrics, alerts, events. Send { t: 'command', d: 'connect:North Gate' } to drive it." },
      ],
    },
  ],
  errors: [
    { code: "200", name: "OK", when: "Request succeeded." },
    { code: "400", name: "Bad Request", when: "Malformed body or parameters." },
    { code: "404", name: "Not Found", when: "Unknown source, subject or detection id." },
    { code: "409", name: "Conflict", when: "Concurrent modification of the same resource." },
    { code: "503", name: "Backend Down", when: "The analysis backend is not ready (starting, no source, model unavailable)." },
  ],
};
