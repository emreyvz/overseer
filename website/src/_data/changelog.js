// Version history (newest first). Each release renders as an expandable entry.
module.exports = {
  releases: [
    { version: "0.9.0", codename: "Spatial Foresight", date: "2026-07-31", tag: "latest",
      changes: [
        { type: "added", text: "Tactical god-view radar with depth-locked contact placement (Depth Anything)." },
        { type: "added", text: "Predictive ghosts overlay with path-convergence detection." },
        { type: "added", text: "Functional DETECTION class filters, gated at the detector and persisted." },
        { type: "changed", text: "Smart Suggestions redesigned as an interactive triage cockpit." },
      ] },
    { version: "0.8.0", codename: "Identity Intelligence", date: "2026-06-20", tag: "",
      changes: [
        { type: "added", text: "Cross-camera Re-ID with gait descriptors and soft biometrics." },
        { type: "added", text: "Long-term subject dossiers and relationship graph." },
        { type: "added", text: "Multi-frame + Real-ESRGAN super-resolution reconstruction." },
        { type: "fixed", text: "Reconstruction rejects mismatched crops to never do worse than zoom." },
      ] },
    { version: "0.7.0", codename: "Spatial Lift", date: "2026-05-05", tag: "",
      changes: [
        { type: "added", text: "3D reconstruction: point cloud + mesh with background completion." },
        { type: "added", text: "three.js spatial viewport with fly-through camera." },
        { type: "changed", text: "Depth pipeline moved to Depth Anything V2 with temporal fusion." },
      ] },
    { version: "0.6.0", codename: "Watchtower", date: "2026-03-18", tag: "",
      changes: [
        { type: "added", text: "Zones, alert rules and correlated threat scoring." },
        { type: "added", text: "Per-camera DNA / reputation and smart suggestions." },
        { type: "added", text: "ANPR, vehicle make (ViT) and ego-compensated speed." },
      ] },
    { version: "0.5.0", codename: "First Light", date: "2026-01-30", tag: "",
      changes: [
        { type: "added", text: "YOLO11 detection + ByteTrack tracking on live feeds." },
        { type: "added", text: "MOG2 motion, recording and the operator POV console." },
        { type: "added", text: "FastAPI bridge with WebSocket streaming and SQLite persistence." },
      ] },
  ],
};
