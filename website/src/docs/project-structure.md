---
title: Project Structure
order: 6
intro: "A map of the repository so you know where things live."
---

```text
overseer/
├─ main.py             # backend entry point (FastAPI bridge)
├─ config/
│  └─ default.yaml     # configuration
├─ server/             # orchestrator, API, backend logic
│  ├─ backend.py       # capture → analysis → fan-out
│  ├─ app.py           # FastAPI routes + WebSocket
│  ├─ spatial.py       # depth → point cloud contract
│  ├─ roster.py        # cross-camera Re-ID / dossiers
│  └─ ...
├─ ai/                 # model backends
│  └─ yolo.py          # YOLO11 detection + tracking
├─ vision/
│  └─ motion.py        # MOG2 motion detector
├─ storage/
│  └─ database.py      # SQLite persistence
├─ models/             # cached model weights (gitignored)
├─ web/                # Svelte 5 + Electron frontend
│  └─ src/             # UI components, stores, lib
└─ website/            # this documentation site (Eleventy)
   └─ src/
      ├─ _data/        # content data (models, pipeline, ...)
      ├─ _includes/    # layouts + partials
      └─ docs/         # Markdown docs (you are here)
```

## Backend flow

`StreamReader` reads a source into a `FrameBuffer`; an `AnalysisWorker` runs the model pipeline and emits an `AnalysisResult` that an event bus fans out to recording, alerting, storage and the API stream. See [Architecture]({{ '/architecture/' | url }}).
