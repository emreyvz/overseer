#!/usr/bin/env bash
# Overseer - setup and launch (macOS / Linux)
set -e
cd "$(dirname "$0")"

echo
echo "  ================================================"
echo "     OVERSEER   -   setup and launch"
echo "  ================================================"
echo

# ---- 1. make sure uv (the Python toolchain) is installed ----
if ! command -v uv >/dev/null 2>&1; then
  echo "  [setup] Installing uv (one time)..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "  ERROR: could not install uv automatically."
  echo "  Install it from https://github.com/astral-sh/uv and run this again."
  exit 1
fi

echo "  [1/3] Installing Python dependencies (first run downloads a lot, please wait)..."
uv sync

# ---- fetch AI models (best effort; never blocks launch, optional parts skip) ----
echo "  [setup] Enabling plate reading (ANPR) — optional..."
uv sync --extra ai-extras || true
echo "  [setup] Fetching AI models (one time; the app still runs if this is skipped)..."
uv run python -m match.tools.export_models || true

open_browser() {
  ( sleep 5; (open "http://127.0.0.1:8787" 2>/dev/null || xdg-open "http://127.0.0.1:8787" 2>/dev/null) ) &
}

# ---- 2. Node present -> full desktop app; otherwise -> browser ----
if command -v npm >/dev/null 2>&1; then
  echo "  [2/3] Building the interface..."
  cd web
  [ -d node_modules ] || npm install
  npm run build
  echo "  [3/3] Launching the Overseer desktop app..."
  npm run electron
else
  echo "  Node.js was not found, starting Overseer in your web browser instead."
  echo "  (Install Node.js from https://nodejs.org if you want the desktop window.)"
  echo "  [2/3] Opening the browser shortly..."
  open_browser
  echo "  [3/3] Starting Overseer. Press Ctrl+C to stop it."
  uv run python -m server
fi
