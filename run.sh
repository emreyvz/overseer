#!/usr/bin/env bash
# Overseer quick start. Launches the backend (FastAPI bridge) at http://127.0.0.1:8787.
# Open that URL in a browser, or run the desktop shell with:  cd web && npm run desktop
set -e
cd "$(dirname "$0")"
echo "Starting Overseer backend at http://127.0.0.1:8787 ..."
if [ -x ".venv/bin/python" ]; then
  exec .venv/bin/python main.py
elif [ -x ".venv/Scripts/python.exe" ]; then
  exec .venv/Scripts/python.exe main.py
else
  exec python main.py
fi
