"""Re-ensure the spatial-3D extras after `uv sync`.

The 3D scene view uses ROMP (human mesh recovery) installed with `pip --no-deps` because its own
dependency pins would clobber the pinned base env. Since it's not in `uv.lock`, EVERY `uv sync` /
`uv run` strips it back out — so the app's launchers call this right after `uv sync` to reinstall
it. Idempotent: it only rebuilds when the package is actually missing, so normal launches are fast.
Run with the venv's Python: `.venv/Scripts/python.exe scripts/ensure_spatial_extras.py`.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys


def have(mod: str) -> bool:
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:  # noqa: BLE001
        return False


def uv_install(args: list[str]) -> None:
    subprocess.run(["uv", "pip", "install", "--python", sys.executable, *args], check=False)


def main() -> None:
    if have("romp"):
        print("[spatial] ROMP present")
        return
    print("[spatial] installing ROMP (3D human bodies) — one-time build...")
    uv_install(["Cython"])                                             # build dep for simple_romp
    uv_install(["--no-deps", "--no-build-isolation", "simple_romp==1.1.4"])
    print("[spatial] ROMP ready" if have("romp")
          else "[spatial] ROMP install failed — the view still runs, just without 3D bodies")


if __name__ == "__main__":
    main()
