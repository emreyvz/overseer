# Sets up DUSt3R for the multi-view full-3D reconstruction (spatial.multiview).
# Clones the repo (with its croco submodule) into third_party/dust3r and installs the few extra
# Python deps. The ViT-Large weights (~2 GB) download lazily from HuggingFace on first use.
# Pure PyTorch — no CUDA Toolkit / compiler required.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$dst = Join-Path $root "third_party\dust3r"
if (-not (Test-Path (Join-Path $dst "dust3r\model.py"))) {
  Write-Host "Cloning DUSt3R (+ croco submodule) into third_party/dust3r ..."
  git clone --recursive --depth 1 https://github.com/naver/dust3r $dst
} else {
  Write-Host "third_party/dust3r already present."
}
Write-Host "Installing Python deps (roma, einops, trimesh) ..."
uv pip install roma einops trimesh
Write-Host "Done. Multi-view full-3D is ready (weights download on first reconstruction)."
