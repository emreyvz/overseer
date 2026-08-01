---
title: Requirements
order: 2
intro: "What Overseer needs to run. A GPU is optional but strongly recommended for depth, reconstruction and super-resolution."
---

## Software

| Component | Version | Notes |
| --- | --- | --- |
| Python | 3.11 or 3.12 | Backend and inference |
| Node.js | 18+ (20+ recommended) | Frontend / desktop shell |
| CUDA | 12.x | Optional, for GPU acceleration |
| Git | any recent | To clone the repository |

## Hardware

| Tier | CPU | GPU | RAM | Use |
| --- | --- | --- | --- | --- |
| Minimum | 4 cores | none (CPU fallback) | 8 GB | Detection + tracking at reduced FPS |
| Recommended | 8 cores | NVIDIA, 6 GB VRAM | 16 GB | Live detection + depth + 3D |
| Comfortable | 8+ cores | NVIDIA, 8–12 GB VRAM | 32 GB | Multi-model, reconstruction, super-resolution |

## Notes

- Depth (Depth Anything V2) and super-resolution (Real-ESRGAN) run far faster on an NVIDIA GPU with FP16. Without a GPU they still work, just slower.
- Models are downloaded once and cached locally under <code>models/</code>. After the first run, Overseer can operate fully offline.
- Storage grows with recordings and snapshots; point the data directory at a disk with room.

<div class="callout"><div class="c-title">Next</div><p>Ready? Continue to <a href="{{ '/docs/installation/' | url }}">Installation</a>.</p></div>
