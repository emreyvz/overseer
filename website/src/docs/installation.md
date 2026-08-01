---
title: Installation
order: 1
intro: "Clone the repo and run one command. It sets up dependencies, models and the interface for you."
---

## 1. Clone

```bash
git clone https://github.com/emreyvz/overseer.git
cd overseer
```

## 2. One command

The launcher installs the Python toolchain ([uv](https://github.com/astral-sh/uv)), syncs dependencies, fetches the AI models, builds the interface, and starts the app:

```bash
# macOS / Linux
./overseer.sh

# Windows
overseer.cmd
```

If Node.js is installed you get the desktop app; otherwise Overseer opens in your browser at `http://127.0.0.1:8787`. The first run downloads model weights into `models/`; after that it can run fully offline.

## Manual setup (optional)

Prefer to do it yourself:

```bash
uv sync                    # Python dependencies
cd web && npm install      # frontend dependencies
```

Then run the backend with `python main.py` (or `uv run python -m server`) and the desktop shell with `cd web && npm run desktop`.

<div class="callout warn"><div class="c-title">GPU</div><p>For CUDA acceleration, install a PyTorch build matching your CUDA version. Overseer falls back to CPU automatically if no GPU is found.</p></div>

<div class="callout"><div class="c-title">Next</div><p>Continue to <a href="{{ '/docs/quick-start/' | url }}">Quick Start</a>.</p></div>
