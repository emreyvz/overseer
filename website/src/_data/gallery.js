// Gallery categories. Each item is a labelled placeholder tile (swap `img` in later).
module.exports = {
  categories: [
    { id: "detection", title: "Detection Results",
      items: [
        { label: "Street scene", caption: "4 persons + 6 vehicles, per-box track ids" },
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
        { label: "Tactical god-view", caption: "Top-down radar, depth-locked contacts" },
        { label: "Predictive ghosts", caption: "Near-future positions + convergence" },
        { label: "Density heatmap", caption: "Crowd density over the feed" },
      ] },
    { id: "videos", title: "Videos",
      items: [
        { label: "Live overlay", caption: "Detections + ghosts on a live feed" },
        { label: "Journey supercut", caption: "A subject across cameras, stitched" },
      ] },
    { id: "screenshots", title: "Screenshots",
      items: [
        { label: "POV console", caption: "Operator view with modules rail" },
        { label: "Identity dossier", caption: "Long-term subject profile" },
        { label: "Spatial viewport", caption: "3D reconstruction of a frame" },
      ] },
  ],
};
