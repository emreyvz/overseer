---
title: Quick Start
order: 3
intro: "Run the backend, open the console, connect a source, and see live detections in a couple of minutes."
---

## Start the backend

```bash
python main.py
```

The FastAPI bridge comes up at `http://127.0.0.1:8787`. It serves the REST API, the WebSocket stream and the MJPEG feeds.

## Open the console

Either the desktop shell or a browser:

```bash
# Desktop (Electron)
cd web && npm run desktop

# or just open the served URL in a browser
# http://127.0.0.1:8787
```

## Connect a source

1. On the landing screen choose **Begin Observation**.
2. Pick a camera (a demo source is seeded on a fresh install), or add an RTSP/ONVIF URL under **Manage Sources**.
3. The live feed opens in the POV view with detections overlaid.

## Try the overlays

From the left **Modules** rail:

- Toggle **DETECTION** classes (person / vehicle / animal / weapon). Disabling a class sheds its load across the whole pipeline.
- Turn on **TACTICAL** for the top-down god-view radar and **FORESIGHT** for predictive ghosts.
- Open **3D SPATIAL** to lift the frame into a navigable point cloud.

<div class="callout"><div class="c-title">Tip</div><p>No camera handy? A simulation mode drives synthetic detections in the browser at <code>/?sim</code> for a quick tour of the UI.</p></div>

<div class="callout"><div class="c-title">Next</div><p>Tune behaviour in <a href="{{ '/docs/configuration/' | url }}">Configuration</a>.</p></div>
