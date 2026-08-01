// Worked examples. Each entry becomes an Examples detail page (src/examples/example.njk).
module.exports = [
  {
    slug: "single-image-analysis", title: "Single Image Analysis", tags: ["Image", "Detection"],
    summary: "Push one still image through the full pipeline: detect, segment, depth, and lift to 3D.",
    steps: [
      "Start the backend with `python main.py`.",
      "Register the image as a source, or drop it onto a camera slot.",
      "Read detections, masks and the depth field from `/api/spatial/{id}`.",
      "Optionally reconstruct a face or plate crop for a clearer read.",
    ],
    code: "curl -s http://127.0.0.1:8787/api/spatial/1?grid=320 | jq '.scene.entities'",
    result: "A list of entities with class, image position, depth and confidence, plus a depth grid ready to lift into a point cloud.",
  },
  {
    slug: "video-analysis", title: "Video Analysis", tags: ["Video", "Events"],
    summary: "Batch-analyse a recorded clip into an event timeline, tracklets and exports.",
    steps: [
      "Add the video file as a looped source.",
      "Let the pipeline run; events accumulate in the timeline and SQLite store.",
      "Query events over the REST API or watch them stream over the WebSocket.",
      "Export incident clips or a journey supercut for any subject.",
    ],
    code: "# events stream live over the WebSocket as the clip plays\nwscat -c ws://127.0.0.1:8787/ws",
    result: "A chronological event feed (PERSON, VEHICLE, ANOMALY, WEAPON…) with snapshots and clips.",
  },
  {
    slug: "live-camera-processing", title: "Live Camera Processing", tags: ["Live", "RTSP"],
    summary: "Connect an RTSP/ONVIF camera and analyse it in real time with overlays.",
    steps: [
      "Discover cameras with `POST /api/discover` or add an RTSP URL.",
      "Open the camera in the POV view; the feed streams as MJPEG.",
      "Toggle DETECTION classes and the TACTICAL / FORESIGHT overlays from the modules rail.",
      "Alerts raise as zones and rules trigger.",
    ],
    code: 'curl -X POST http://127.0.0.1:8787/api/sources \\\n  -H "content-type: application/json" \\\n  -d \'{"name":"North Gate","url":"rtsp://…"}\'',
    result: "A live, overlaid feed with detections, predictive ghosts and a depth-locked tactical radar.",
  },
  {
    slug: "image-to-3d-scene", title: "Image to 3D Scene", tags: ["Depth", "3D"],
    summary: "Turn a single frame into a navigable, textured 3D scene.",
    steps: [
      "Call `/api/spatial/{id}` to run Depth Anything V2 and scene completion.",
      "The response carries the RGB grid, depth grid, entities and a completed background layer.",
      "The viewer back-projects the depth through a pinhole model and renders it in three.js.",
      "Fly the camera through the scene; measure and inspect entities.",
    ],
    code: "GET /api/spatial/1?grid=400   # higher grid = denser cloud",
    result: "A coloured point cloud / mesh with the occluded background reconstructed behind objects.",
  },
  {
    slug: "video-to-3d-scene", title: "Video to 3D Scene", tags: ["Video", "3D"],
    summary: "Fuse several frames for cleaner geometry, then reconstruct the scene.",
    steps: [
      "Enable temporal fusion (`spatial.fuse_frames`) so depth noise averages out.",
      "Run the spatial pass on the active camera; the median-fused depth is used.",
      "Reconstruct the scene and export it.",
    ],
    code: "spatial:\n  fuse_frames: 3\n  input_width: 640",
    result: "A steadier point cloud than a single frame, with less monocular flicker on the static scene.",
  },
  {
    slug: "detection-pipeline", title: "Detection Pipeline", tags: ["YOLO11", "Filters"],
    summary: "Run only the classes you care about and shed the rest of the load.",
    steps: [
      "Read the current filters from `/api/detection/filters`.",
      "Disable classes you do not need; the change persists and applies live.",
      "Disabled classes are dropped at the detector, freeing tracking, Re-ID and analytics.",
    ],
    code: 'curl -X POST http://127.0.0.1:8787/api/detection/filters \\\n  -H "content-type: application/json" \\\n  -d \'{"vehicle":false,"animal":false}\'',
    result: "A lighter pipeline that only detects and tracks the enabled classes.",
  },
  {
    slug: "depth-pipeline", title: "Depth Pipeline", tags: ["Depth", "Tactical"],
    summary: "Use the depth field beyond 3D, to place contacts on the tactical radar.",
    steps: [
      "Run the spatial pass to compute the depth grid and FOV.",
      "The tactical god-view samples that depth at each subject's foot point.",
      "Contacts are placed by real scene depth + real FOV (DEPTH-LOCKED), with a heuristic fallback.",
    ],
    code: "# depth field is fetched once per camera and cached (near-static scene)\nGET /api/spatial/1?grid=240",
    result: "A top-down radar where contact distance reflects true scene depth, not a horizon guess.",
  },
];
