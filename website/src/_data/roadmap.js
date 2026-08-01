// Roadmap milestones (newest / current first). `state`: done | now | planned.
module.exports = {
  milestones: [
    { when: "Shipped · 2026 Q3", state: "done", title: "Spatial Foresight",
      points: ["Depth-locked tactical god-view radar", "Predictive ghosts + convergence detection", "Functional, load-shedding DETECTION class filters", "Smart-suggestions triage cockpit"] },
    { when: "Shipped · 2026 Q2", state: "done", title: "Identity Intelligence",
      points: ["Cross-camera Re-ID with gait + soft biometrics", "Long-term subject dossiers", "Multi-frame + learned super-resolution reconstruction", "Relationship graph"] },
    { when: "In progress · 2026 Q4", state: "now", title: "Metric Spatial",
      points: ["Guided camera calibration (intrinsics + ground plane)", "Metric depth and true-scale measurements", "Depth-aware tracking association", "Mesh export (glTF / PLY)"] },
    { when: "Planned · 2027 Q1", state: "planned", title: "Open-Vocabulary Perception",
      points: ["Text-promptable detection (open vocabulary)", "Natural-language standing queries on the live feed", "Attention / gaze field visualization"] },
    { when: "Planned · 2027 Q2", state: "planned", title: "Scale & Fusion",
      points: ["Multi-camera 3D fusion into one scene", "Continuous SLAM-style scene mapping", "Distributed / multi-node inference", "Risk-field ambient analytics"] },
    { when: "Exploring", state: "planned", title: "Edge & Deployment",
      points: ["Quantized (INT8) edge model tiers", "Containerised one-command deploy", "Plugin SDK for custom analysers", "Export/redaction (face + plate blurring)"] },
  ],
};
