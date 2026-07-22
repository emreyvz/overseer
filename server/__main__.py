"""Run the OVERSEER web bridge:  uv run python -m server"""
from __future__ import annotations

from pathlib import Path

import uvicorn

from core.config import load_config


def main() -> None:
    cfg = load_config(Path("config/default.yaml"))
    host = str(cfg.get("rest_api.host", "127.0.0.1"))
    port = int(cfg.get("rest_api.port", 8787))
    uvicorn.run("server.app:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
