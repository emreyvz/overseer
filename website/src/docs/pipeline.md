---
title: Pipeline
order: 9
intro: "How a frame becomes structured intelligence, in thirteen stages. This is the summary; the full breakdown is on the Pipeline page."
---

Every analysed frame flows through the same stages:

1. **Input** &nbsp;·&nbsp; acquire a source (camera, stream, video, image).
2. **Frame Capture** &nbsp;·&nbsp; bounded, drop-oldest buffering.
3. **Preprocessing** &nbsp;·&nbsp; normalise, low-light enhance, background plate.
4. **Detection** &nbsp;·&nbsp; YOLO11 + ByteTrack with class gating.
5. **Segmentation** &nbsp;·&nbsp; mask movers vs the static plate.
6. **Depth Estimation** &nbsp;·&nbsp; Depth Anything V2, temporally fused.
7. **Pose Estimation** &nbsp;·&nbsp; keypoints for intent and gait.
8. **Geometry Fusion** &nbsp;·&nbsp; fuse depth, masks, background completion.
9. **3D Reconstruction** &nbsp;·&nbsp; back-project to a point cloud.
10. **Mesh Optimization** &nbsp;·&nbsp; triangulate + edge culling.
11. **Texture Mapping** &nbsp;·&nbsp; project RGB and inpainted texture.
12. **Spatial Analysis** &nbsp;·&nbsp; foresight, identity, alerts.
13. **Output** &nbsp;·&nbsp; stream, export, persist.

<div class="callout"><div class="c-title">Full detail</div><p>Expand every stage with its inputs, outputs and internals on the <a href="{{ '/pipeline/' | url }}">Pipeline</a> page.</p></div>
