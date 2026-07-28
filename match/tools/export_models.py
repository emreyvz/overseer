"""Fetch/export the match-engine model weights into models/ (git-ignored).

Reproduces exactly how the shipped setup was produced. Each step is independent and
skips work already done, so re-running is cheap. Anything that fails is reported and
skipped — the engine still runs (baseline / DINOv2 fallback), just with less capability.

    uv run python -m match.tools.export_models            # all steps
    uv run python -m match.tools.export_models --only seg # one step

Steps:
  seg     YOLO-seg foreground model                 -> models/yolo11n-seg.pt
  dinov2  DINOv2 ViT-S/14 generic embedder (TS)     -> models/dinov2_vits14.torchscript
  osnet   OSNet person ReID (needs torchreid)       -> models/osnet_x1_0.torchscript
  easyocr trigger EasyOCR model download (ANPR)     -> ~/.EasyOCR cache

Dedicated person/vehicle ReID (OSNet / a VeRi-trained model) give the best identity
accuracy. If their TorchScript files are present the engine prefers them; otherwise it
uses the DINOv2 embedder, which is still far stronger than colour matching.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

MODELS = Path("models")


def _export_seg() -> str:
    dst = MODELS / "yolo11n-seg.pt"
    if dst.exists():
        return f"seg: already present ({dst})"
    from ultralytics import YOLO
    YOLO("yolo11n-seg.pt")                       # auto-downloads to CWD
    shutil.move("yolo11n-seg.pt", str(dst))
    return f"seg: downloaded -> {dst}"


def _export_dinov2() -> str:
    dst = MODELS / "dinov2_vits14.torchscript"
    if dst.exists():
        return f"dinov2: already present ({dst})"
    import torch
    model = torch.hub.load("facebookresearch/dinov2", "dinov2_vits14",
                           trust_repo=True).eval()
    dummy = torch.zeros(1, 3, 224, 224)
    torch.jit.trace(model, dummy).save(str(dst))
    return f"dinov2: exported -> {dst}"


def _export_osnet() -> str:
    dst = MODELS / "osnet_x1_0.torchscript"
    if dst.exists():
        return f"osnet: already present ({dst})"
    try:
        import torch
        import torchreid
    except Exception:
        return ("osnet: SKIPPED - `uv pip install torchreid` and provide ReID-trained "
                "weights, then re-run")
    model = torchreid.models.build_model("osnet_x1_0", num_classes=1000, pretrained=True)
    model.eval()
    # NOTE: pretrained=True loads ImageNet weights. For true ReID accuracy, load
    # market1501/veri weights via torchreid.utils.load_pretrained_weights(model, path)
    # before export.
    dummy = torch.zeros(1, 3, 256, 128)
    torch.jit.trace(model, dummy).save(str(dst))
    return f"osnet: exported -> {dst} (verify ReID weights were loaded)"


def _export_easyocr() -> str:
    try:
        import easyocr
        easyocr.Reader(["en"], gpu=True, verbose=False)   # downloads + caches models
        return "easyocr: models cached (~/.EasyOCR)"
    except Exception as exc:  # noqa: BLE001
        return f"easyocr: SKIPPED - `uv pip install easyocr` ({type(exc).__name__})"


_STEPS = {"seg": _export_seg, "dinov2": _export_dinov2,
          "osnet": _export_osnet, "easyocr": _export_easyocr}


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch/export match-engine models")
    ap.add_argument("--only", choices=list(_STEPS), default=None,
                    help="run a single step (default: all)")
    args = ap.parse_args()
    MODELS.mkdir(exist_ok=True)
    steps = [args.only] if args.only else list(_STEPS)
    for name in steps:
        try:
            print(_STEPS[name]())
        except Exception as exc:  # noqa: BLE001
            print(f"{name}: FAILED - {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
