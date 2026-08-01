---
title: CLI
order: 5
intro: "The commands you will actually use, on the backend and the frontend."
---

## Backend

```bash
python main.py            # start the FastAPI bridge (http://127.0.0.1:8787)
```

The backend serves REST, the WebSocket stream and MJPEG feeds. It loads models on first use and persists to a local SQLite database.

## Frontend (in web/)

```bash
npm install               # install dependencies
npm run dev               # Vite dev server (browser, with hot reload)
npm run build             # production build to web/dist
npm run desktop           # build + launch the Electron desktop shell
npm run check             # type-check (svelte-check + tsc)
```

## Documentation site (in website/)

```bash
npm install               # install Eleventy
npm run dev               # local preview with live reload
npm run build             # static build to website/_site
```

<div class="callout"><div class="c-title">Simulation</div><p>Append <code>?sim</code> to the frontend URL to drive the UI with synthetic detections, no backend or camera required.</p></div>
