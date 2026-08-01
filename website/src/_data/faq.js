// FAQ, grouped. Rendered as accordions on the FAQ page and referenced from the docs.
module.exports = {
  groups: [
    {
      title: "General",
      items: [
        { q: "What is Overseer?", a: "Overseer is an AI computer-vision and spatial-intelligence platform. It turns ordinary camera feeds into structured understanding: real-time detection and tracking, monocular depth, 3D reconstruction, cross-camera re-identification and event analytics." },
        { q: "Who is it for?", a: "Security operators, researchers and builders who want on-device situational awareness and 3D scene understanding without sending footage to the cloud." },
        { q: "Is it open source?", a: "Yes, the project lives on GitHub under the MIT license. See the repository for the current source and issues." },
      ],
    },
    {
      title: "Privacy & Deployment",
      items: [
        { q: "Does it send data to the cloud?", a: "No. All inference runs on the host and models are vendored locally, so Overseer can run fully offline / air-gapped. Footage, embeddings and events stay in a local SQLite store on disk." },
        { q: "Can it run without a GPU?", a: "Yes, with reduced throughput. Detection and tracking degrade gracefully to CPU; depth and super-resolution are much faster on an NVIDIA GPU (CUDA + FP16)." },
        { q: "How is authentication handled?", a: "The service binds to localhost with no auth by default. For networked or multi-user setups, front it with a reverse proxy that enforces your auth (bearer token, mTLS, SSO)." },
      ],
    },
    {
      title: "Capabilities",
      items: [
        { q: "Which cameras are supported?", a: "RTSP and ONVIF network cameras (with LAN discovery), local video files and single images. Looped clips are handy for testing." },
        { q: "How accurate is the depth / 3D?", a: "Monocular depth (Depth Anything V2) is relative and generalises well. It is not metric without calibration; a guided calibration flow on the roadmap upgrades it to true scale." },
        { q: "Can I disable models I don't need?", a: "Yes. DETECTION class filters are gated at the detector, so turning off a class removes it from detection, tracking, Re-ID and analytics, real load shedding, and the setting persists." },
      ],
    },
    {
      title: "Contributing",
      items: [
        { q: "How do I report a bug or request a feature?", a: "Open an issue on the GitHub repository with steps to reproduce or a clear description of the request." },
        { q: "How do I add documentation?", a: "Docs are Markdown files under website/src/docs/. Add a file with front matter (title, order, tag: docs) and it appears in the sidebar automatically." },
      ],
    },
  ],
};
