#!/usr/bin/env bash
# Sets up DUSt3R for the multi-view full-3D reconstruction (spatial.multiview).
# Clones the repo (with its croco submodule) into third_party/dust3r and installs the few extra
# Python deps. The ViT-Large weights (~2 GB) download lazily from HuggingFace on first use.
# Pure PyTorch — no CUDA Toolkit / compiler required.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DST="$ROOT/third_party/dust3r"
if [ ! -f "$DST/dust3r/model.py" ]; then
  echo "Cloning DUSt3R (+ croco submodule) into third_party/dust3r ..."
  git clone --recursive --depth 1 https://github.com/naver/dust3r "$DST"
else
  echo "third_party/dust3r already present."
fi
echo "Installing Python deps (roma, einops, trimesh) ..."
uv pip install roma einops trimesh
echo "Done. Multi-view full-3D is ready (weights download on first reconstruction)."
