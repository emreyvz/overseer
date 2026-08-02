// Version history (newest first). Each release renders as an expandable entry.
module.exports = {
  releases: [
    { version: "0.12.1", codename: "Fourth Dimension", date: "2026-08-02", tag: "latest",
      changes: [
        { type: "improved", text: "Much smoother live video: the displayed feed now runs at camera rate, decoupled from the (slower) analysis pipeline, with detection boxes interpolated on top. No loss of analysis quality." },
        { type: "fixed", text: "Chronoscape now works: it freezes a fixed clip of the recorded history on entry (no more markers drifting over the frozen scene), scopes trails to the camera, drops standing-still jitter, and says so when there is no movement to replay yet." },
        { type: "fixed", text: "Social X-ray is conservative: it only links people with real evidence (facing each other, watching, approaching). People merely standing side by side, like a queue, are no longer labelled as together." },
      ] },
    { version: "0.12.0", codename: "Fourth Dimension", date: "2026-08-02", tag: "",
      changes: [
        { type: "added", text: "Chronoscape: a 4D time-travel replay inside the 3D view. Every tracked subject's recent path is lifted onto the reconstructed ground as an age-graded trail, with playheads that glide along each path. Scrub or play the scene's last minutes through time." },
        { type: "added", text: "Social X-ray: each person's attention direction as a cone, and live links between people who are interacting (engaged, watching, together, approaching), from a pose-derived facing that works even when they stand still." },
        { type: "added", text: "Both are driven from the operator by voice or text (\"time travel\", \"social x-ray\") and the modules rail; S toggles Social X-ray, the CHRONO button opens time-travel." },
      ] },
    { version: "0.11.2", codename: "Acuity", date: "2026-08-02", tag: "",
      changes: [
        { type: "added", text: "Vehicle body type (sedan, hatchback, SUV, station wagon, pickup, van, minibus...) via zero-shot CLIP, shown on cards and filterable in the roster." },
        { type: "added", text: "Estimated person stature (band plus approximate cm), shown on the card and filterable." },
        { type: "added", text: "More read behaviours: sitting, lying, swimming, standing, approaching and moving away, from posture, motion and apparent size." },
        { type: "improved", text: "Clothing colour reads from the torso only and is shown only when it is clearly that colour, so a murky crop no longer gets a wrong one; a shirtless torso is reported as bare skin." },
        { type: "improved", text: "Live Enhance is sharper and more natural: multi-pass reconstruction fitted to the loupe, no cartoon or pixel blocks." },
        { type: "improved", text: "Turkish speech-to-text accuracy, on a larger on-device Whisper model." },
        { type: "fixed", text: "Voice and body type no longer fail on a full GPU (speech-to-text falls back to CPU, body type runs on CPU). Storage counts every snapshot and clip on disk. The operator answers on-screen visual questions by looking at the frame." },
      ] },
    { version: "0.10.0", codename: "AI Operator", date: "2026-08-02", tag: "",
      changes: [
        { type: "added", text: "AI Operator: run the whole app by voice or text, 70+ chained actions with data passing between steps, and vision Q&A about what is on screen." },
        { type: "added", text: "Offline voice: on-device Whisper speech-to-text (Turkish + English) and spoken replies." },
        { type: "added", text: "Live narration, follow-cam, and occlusion x-ray." },
        { type: "added", text: "Live 'enhance': box-select any region for a photographic close-up." },
        { type: "added", text: "Auto zone detection surfaced in Smart Suggestions — proposed, editable, and explained." },
        { type: "fixed", text: "Colour naming, vehicle attributes, storage stats, forensic precision and detection-box alignment." },
      ] },
    { version: "0.9.0", codename: "Spatial Foresight", date: "2026-07-31", tag: "",
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
