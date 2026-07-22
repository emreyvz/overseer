"""Overseer entry point: launches the Overseer web bridge (FastAPI + Svelte UI).

The application front-end is the Overseer web UI in `web/`, served by the FastAPI
bridge in `server/`. `python main.py` starts the bridge; open the desktop shell
with `cd web && npm run desktop`, or a browser at the server URL (default
http://127.0.0.1:8787).
"""
from __future__ import annotations

from server.__main__ import main

if __name__ == "__main__":
    main()
