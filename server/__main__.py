"""Run the OVERSEER web bridge:  uv run python -m server"""
from __future__ import annotations

# Cap OpenMP/BLAS thread fan-out BEFORE numpy/torch/cv2 load, so a single analysis op cannot grab
# every core and starve the 30fps display threads (the runtime cap in server.app._tune_thread_pools
# covers other launch paths). Leave two cores for the display / capture / stream path.
import os as _os

_n = max(1, (_os.cpu_count() or 4) - 2)
for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    _os.environ.setdefault(_v, str(_n))

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
