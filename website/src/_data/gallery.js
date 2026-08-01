// Gallery categories. Each item is a labelled placeholder tile (swap `img` in later).
module.exports = {
  categories: [
    { id: "detection", title: "Detection Results",
      items: [
        { label: "Operator console", caption: "Detection overlays with stable track ids on a live feed", img: "/assets/img/shots/dashboard.webp" },
        { label: "Night recall", caption: "Low-light enhance recovering faint pedestrians" },
        { label: "Tiled pass", caption: "Small distant objects recovered via tiling" },
      ] },
    { id: "depth", title: "Depth Maps",
      items: [
        { label: "Disparity field", caption: "Depth Anything V2, near = bright" },
        { label: "Fused depth", caption: "Median-fused across frames, low noise" },
        { label: "Ground plane", caption: "Structure-aware depth behind occluders" },
      ] },
    { id: "segmentation", title: "Segmentation",
      items: [
        { label: "Foreground mask", caption: "Movers lifted off the static plate" },
        { label: "Instance masks", caption: "Per-object silhouettes" },
      ] },
    { id: "meshes", title: "3D Meshes",
      items: [
        { label: "Scene mesh", caption: "Triangulated surface with edge culling" },
        { label: "Background layer", caption: "Reconstructed wall / floor behind objects" },
      ] },
    { id: "point-clouds", title: "Point Clouds",
      items: [
        { label: "Coloured cloud", caption: "Depth back-projected with RGB" },
        { label: "Fly-through", caption: "Navigable three.js viewport" },
      ] },
    { id: "spatial", title: "Spatial Maps",
      items: [
        { label: "Tactical god-view", caption: "Top-down radar with depth-locked contacts + ghosts", img: "/assets/img/shots/dashboard.webp" },
        { label: "Density heatmap", caption: "Crowd density over the live feed", img: "/assets/img/shots/heatmap.webp" },
        { label: "Predictive ghosts", caption: "Near-future positions and path convergence" },
      ] },
    { id: "videos", title: "Videos",
      items: [
        { label: "Live overlay", caption: "Detections + ghosts on a live feed" },
        { label: "Journey supercut", caption: "A subject across cameras, stitched" },
      ] },
    { id: "screenshots", title: "Screenshots",
      items: [
        { label: "POV console", caption: "Operator view with modules rail, overlays and alerts", img: "/assets/img/shots/dashboard.webp" },
        { label: "Command center", caption: "Every source on a live map", img: "/assets/img/shots/map.webp" },
        { label: "Density analytics", caption: "Heatmap + per-camera metrics", img: "/assets/img/shots/heatmap.webp" },
      ] },
  ],
};
