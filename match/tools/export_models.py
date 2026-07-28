"""Fetch/export the match-engine model weights into models/ (git-ignored).

Reproduces exactly how the shipped setup was produced. Each step is independent and
skips work already done, so re-running is cheap. Anything that fails is reported and
skipped — the engine still runs (baseline / DINOv2 fallback), just with less capability.

    uv run python -m match.tools.export_models            # all steps
    uv run python -m match.tools.export_models --only seg # one step

Steps:
  seg     YOLO-seg foreground model                 -> models/yolo11n-seg.pt
  dinov2  DINOv2 ViT-S/14 generic embedder (TS)     -> models/dinov2_vits14.torchscript
  osnet   OSNet-AIN person ReID (needs torchreid)   -> models/osnet_ain_x1_0.torchscript
  veri    fast-reid VeRi vehicle ReID (FASTREID_ROOT) -> models/veri_sbs_R50-ibn.torchscript
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


def _find_osnet_ain_def() -> Path | None:
    """Locate torchreid's self-contained osnet_ain.py in site-packages. It only imports
    torch, so we load it in isolation and never trigger torchreid's heavy package init
    (which pulls tensorboard/gdown/etc.)."""
    import site
    roots = list(site.getsitepackages()) + [site.getusersitepackages()]
    for root in roots:
        for rel in ("torchreid/reid/models/osnet_ain.py", "torchreid/models/osnet_ain.py"):
            p = Path(root) / rel
            if p.exists():
                return p
    return None


def _export_osnet() -> str:
    """Convert an OSNet-AIN ReID checkpoint (.pth.tar, e.g. osnet_ain_ms_d_c from the
    torchreid MODEL_ZOO) that the operator dropped in models/ into a TorchScript embedder.
    Best for surveillance: OSNet-AIN is domain-generalizable to unseen cameras."""
    dst = MODELS / "osnet_ain_x1_0.torchscript"
    if dst.exists():
        return f"osnet: already present ({dst})"
    ckpts = (sorted(MODELS.glob("*osnet*ain*.pth*")) or sorted(MODELS.glob("*osnet*.pth*")))
    if not ckpts:
        return ("osnet: SKIPPED - place an OSNet-AIN .pth.tar in models/ (torchreid "
                "MODEL_ZOO), then re-run")
    defn = _find_osnet_ain_def()
    if defn is None:
        return "osnet: SKIPPED - `uv pip install torchreid gdown` (for the model def)"
    import importlib.util
    import torch
    spec = importlib.util.spec_from_file_location("osnet_ain", str(defn))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ckpt = torch.load(str(ckpts[0]), map_location="cpu", weights_only=False)
    sd = {k.replace("module.", ""): v for k, v in ckpt["state_dict"].items()}
    n_cls = sd["classifier.weight"].shape[0]        # match the training set's class count
    model = mod.osnet_ain_x1_0(num_classes=n_cls, pretrained=False)
    missing, _ = model.load_state_dict(sd, strict=False)
    model.eval()
    with torch.no_grad():                            # eval() forward returns the embedding
        torch.jit.trace(model, torch.zeros(1, 3, 256, 128)).save(str(dst))
    return f"osnet: exported {ckpts[0].name} -> {dst} (missing keys: {len(missing)})"


def _export_veri() -> str:
    """Convert a fast-reid VeRi vehicle-ReID checkpoint to TorchScript. Unlike OSNet,
    fast-reid models are built by their own framework, so this needs the fast-reid repo
    (set FASTREID_ROOT to a clone) and the checkpoint in models/. The model normalizes
    internally (raw [0,255] RGB) — the engine feeds it accordingly (match.vehicle_builtin_norm)."""
    import os
    dst = MODELS / "veri_sbs_R50-ibn.torchscript"
    if dst.exists():
        return f"veri: already present ({dst})"
    ckpts = sorted(MODELS.glob("*veri*.pth")) + sorted(MODELS.glob("*veri*.pth.tar"))
    if not ckpts:
        return ("veri: SKIPPED - download veri_sbs_R50-ibn.pth (fast-reid releases) into "
                "models/, then re-run")
    root = os.environ.get("FASTREID_ROOT")
    if not root or not Path(root, "fastreid").exists():
        return ("veri: SKIPPED - set FASTREID_ROOT to a fast-reid clone "
                "(git clone https://github.com/JDAI-CV/fast-reid) and re-run")
    import sys

    import torch
    sys.path.insert(0, root)
    from fastreid.config import get_cfg
    from fastreid.modeling.meta_arch import build_model
    from fastreid.utils.checkpoint import Checkpointer
    cfg = get_cfg()
    cfg.merge_from_file(str(Path(root, "configs/VeRi/sbs_R50-ibn.yml")))
    cfg.MODEL.WEIGHTS = str(ckpts[0])
    cfg.MODEL.DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
    cfg.MODEL.BACKBONE.PRETRAIN = False
    if cfg.MODEL.HEADS.POOL_LAYER == "FastGlobalAvgPool":
        cfg.MODEL.HEADS.POOL_LAYER = "GlobalAvgPool"
    cfg.freeze()
    model = build_model(cfg)
    Checkpointer(model).load(cfg.MODEL.WEIGHTS)
    if hasattr(model.backbone, "deploy"):
        model.backbone.deploy(True)
    model.eval()
    h, w = cfg.INPUT.SIZE_TEST
    dummy = torch.randn(1, 3, h, w).to(model.device)
    torch.jit.trace(model, dummy).save(str(dst))
    return f"veri: exported {ckpts[0].name} -> {dst} (raw [0,255] input, 2048-d)"


def _export_easyocr() -> str:
    try:
        import easyocr
        easyocr.Reader(["en"], gpu=True, verbose=False)   # downloads + caches models
        return "easyocr: models cached (~/.EasyOCR)"
    except Exception as exc:  # noqa: BLE001
        return f"easyocr: SKIPPED - `uv pip install easyocr` ({type(exc).__name__})"


_STEPS = {"seg": _export_seg, "dinov2": _export_dinov2, "osnet": _export_osnet,
          "veri": _export_veri, "easyocr": _export_easyocr}


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
