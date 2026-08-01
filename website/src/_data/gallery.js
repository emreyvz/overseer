// Gallery categories. Real captures from the app; click any tile to enlarge (lightbox).
module.exports = {
  categories: [
    { id: "console", title: "Console & Detection",
      items: [
        { label: "Operator console", caption: "Detection overlays with stable track ids", img: "/assets/img/shots/detect.webp" },
        { label: "Target tracking", caption: "A locked target followed across the frame", img: "/assets/img/shots/g-track.webp" },
        { label: "Camera detail", caption: "Per-camera view and image processing", img: "/assets/img/shots/g-camera.webp" },
      ] },
    { id: "depth-3d", title: "Depth & 3D",
      items: [
        { label: "Scene reconstruction", caption: "A frame lifted into a 3D point cloud", img: "/assets/img/shots/spatial.webp" },
        { label: "Reconstruction sweep", caption: "Depth estimated and lifted, live", img: "/assets/img/shots/recon-poster.webp" },
      ] },
    { id: "spatial", title: "Spatial & Analytics",
      items: [
        { label: "Tactical god-view", caption: "Top-down radar with depth-locked contacts", img: "/assets/img/shots/dock.webp" },
        { label: "Density heatmap", caption: "Crowd density over a live feed", img: "/assets/img/shots/density.webp" },
        { label: "Threat report", caption: "Correlated anomaly and threat detection", img: "/assets/img/shots/g-threat.webp" },
      ] },
    { id: "identity", title: "Identity",
      items: [
        { label: "Subject profile", caption: "Who, when and where a subject was seen", img: "/assets/img/shots/profile.webp" },
        { label: "Watchlist", caption: "Flagged subjects and re-sighting hits", img: "/assets/img/shots/g-watchlist.webp" },
      ] },
    { id: "operations", title: "Operations",
      items: [
        { label: "Command center", caption: "Every source on a live map", img: "/assets/img/shots/map.webp" },
        { label: "Camera management", caption: "Add, discover and organise sources", img: "/assets/img/shots/g-cams.webp" },
        { label: "Alert creation", caption: "Build zones and alert rules", img: "/assets/img/shots/g-alert.webp" },
        { label: "Forensic search", caption: "Find a subject across the record", img: "/assets/img/shots/g-forensic.webp" },
        { label: "AI assistant", caption: "Natural-language queries over events", img: "/assets/img/shots/g-assistant.webp" },
        { label: "Landing", caption: "The operator entry screen", img: "/assets/img/shots/g-boot.webp" },
      ] },
  ],
};
