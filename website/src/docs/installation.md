---
title: Installation
order: 1
intro: "Clone the repository, set up a Python environment, install the frontend, and you are ready to run."
---

## 1. Clone

```bash
git clone https://github.com/emreyvz/overseer.git
cd overseer
```

## 2. Python backend

Create a virtual environment and install dependencies:

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

The first backend run downloads and caches the model weights under `models/`. After that, Overseer can run offline.

## 3. Frontend / desktop shell

```bash
cd web
npm install
```

## 4. Verify

Start the backend, then open the desktop shell (see [Quick Start]({{ '/docs/quick-start/' | url }})):

```bash
python main.py            # backend at http://127.0.0.1:8787
cd web && npm run desktop # Electron desktop app
```

<div class="callout warn"><div class="c-title">GPU</div><p>For CUDA acceleration, install a PyTorch build matching your CUDA version. Overseer falls back to CPU automatically if no GPU is found.</p></div>

<div class="callout"><div class="c-title">Next</div><p>Continue to <a href="{{ '/docs/quick-start/' | url }}">Quick Start</a>.</p></div>
