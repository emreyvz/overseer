// Technology stack, grouped into the seven areas on the Technology page.
module.exports = {
  sections: [
    {
      id: "computer-vision", title: "Computer Vision",
      intro: "Detection, tracking, segmentation and re-identification that turn pixels into structured entities.",
      items: [
        { name: "YOLO11", role: "Object detection", note: "Ultralytics YOLO11 (n/s tiers) with per-class confidence floors, tiled inference for small objects, and detector-level class gating." },
        { name: "ByteTrack", role: "Multi-object tracking", note: "Associates high- and low-score boxes for stable ids through occlusion, with a coasting layer for momentary drops." },
        { name: "YOLO11-seg", role: "Segmentation", note: "Instance / foreground masks that separate movers from the static plate for scene completion." },
        { name: "MOG2", role: "Motion detection", note: "Background-subtraction motion mask feeding motion-percentage, recording triggers and the heatmap." },
        { name: "Appearance ReID + Gait", role: "Re-identification", note: "Embedding-based appearance matching plus z-scored gait descriptors and soft biometrics for cross-camera identity." },
      ],
    },
    {
      id: "machine-learning", title: "Machine Learning",
      intro: "The model runtime, from monocular depth to learned super-resolution and vehicle attributes.",
      items: [
        { name: "Depth Anything V2", role: "Monocular depth", note: "Relative inverse-depth per frame, temporally median-fused; the backbone of the spatial pipeline." },
        { name: "Real-ESRGAN", role: "Super-resolution", note: "Vendored SRVGGNetCompact (realesr-general-x4v3) 4x upscaler for reconstructing blurry faces and plates." },
        { name: "ViT classifier", role: "Vehicle attributes", note: "A vision transformer estimates vehicle make, confidence-gated and voted across frames." },
        { name: "Pose estimation", role: "Keypoints", note: "Body keypoints for intent, fall / posture cues and gait sampling." },
        { name: "PyTorch + Ultralytics", role: "Runtime", note: "Torch 2.x with CUDA; models are vendored so weights load with no package sprawl." },
      ],
    },
    {
      id: "geometry-processing", title: "Geometry Processing",
      intro: "Turning depth fields into clean, navigable geometry.",
      items: [
        { name: "Pinhole back-projection", role: "Lift to 3D", note: "Depth grid unprojected through a pinhole model into a coloured point cloud." },
        { name: "Depth smoothing", role: "Continuity", note: "Ramps depth jumps so surfaces stay continuous; straddling triangles across discontinuities are culled." },
        { name: "Ground-plane fitting", role: "Structure", note: "Fits disp = a + b·y + c·x behind occluders for structure-aware background depth." },
        { name: "ECC alignment + median fusion", role: "Reconstruction", note: "Sub-pixel frame alignment then median fusion for multi-frame face / plate reconstruction." },
        { name: "TELEA inpainting", role: "Completion", note: "Fills depth and texture behind removed foreground objects for a continuous background layer." },
      ],
    },
    {
      id: "spatial-computing", title: "Spatial Computing",
      intro: "Reasoning about position, motion and identity across space and time.",
      items: [
        { name: "Foresight engine", role: "Prediction", note: "Per-track ground-plane velocity and short-horizon position prediction; flags converging paths." },
        { name: "Tactical god-view", role: "Top-down radar", note: "Inverse-perspective projection places contacts on a bird's-eye scope using real depth + FOV." },
        { name: "Ego-motion compensation", role: "Camera pose", note: "Per-frame flow model recovers camera motion so object speeds are ground-relative." },
        { name: "Cross-camera Re-ID", role: "Identity", note: "Embeddings + gait + soft biometrics link subjects across cameras into long-term dossiers." },
      ],
    },
    {
      id: "rendering", title: "Rendering",
      intro: "How scenes, overlays and 3D reconstructions reach the screen.",
      items: [
        { name: "three.js", role: "3D viewport", note: "Renders the point cloud / mesh scene with a fly-through camera, fog and depth-tinted sky." },
        { name: "WebGL feed layer", role: "Live overlay", note: "GPU-composited camera feed under the detection, ghost and heatmap overlays." },
        { name: "SVG / Canvas HUD", role: "Overlays", note: "Detection reticles, the tactical radar and density heatmaps drawn as lightweight vector / canvas layers." },
        { name: "Svelte 5 + Electron", role: "Shell", note: "A runes-based Svelte UI in an Electron desktop shell, or any modern browser against the backend." },
      ],
    },
    {
      id: "data-processing", title: "Data Processing",
      intro: "The backend that moves frames, events and identities.",
      items: [
        { name: "FastAPI bridge", role: "API + WS", note: "An async FastAPI server streams frames, detections, metrics and alerts over WebSocket and exposes REST." },
        { name: "Threaded capture", role: "Ingest", note: "StreamReader → drop-oldest FrameBuffer → AnalysisWorker, with results marshalled onto the event loop." },
        { name: "SQLite store", role: "Persistence", note: "Sources, events, alerts, subjects, dossiers and settings persist locally; no external database." },
        { name: "Event bus", role: "Fan-out", note: "A pub/sub bus fans analysis results to recording, alerting, the timeline and the API layer." },
      ],
    },
    {
      id: "performance", title: "Performance Optimizations",
      intro: "Where the latency budget is spent, and how it is kept low.",
      items: [
        { name: "FP16 on CUDA", role: "Throughput", note: "Half-precision inference for detection, depth and super-resolution when a GPU is present." },
        { name: "Frame interval", role: "Adaptive load", note: "Heavy passes run every N frames; results are cached and coasted between runs." },
        { name: "Class gating", role: "Load shedding", note: "Disabled detection classes are dropped at the detector, freeing tracking, Re-ID and analytics too." },
        { name: "Low-res fuse, hi-res finish", role: "Reconstruction", note: "Alignment and fusion run at capped resolution, then a single super-resolution pass upscales." },
        { name: "Bounded buffers", role: "Backpressure", note: "Drop-oldest frame buffers and rolling windows keep memory flat under load." },
      ],
    },
  ],
};
