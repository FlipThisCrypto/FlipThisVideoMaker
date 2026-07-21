#!/usr/bin/env python3
"""Strict JSON CLI for an externally installed official LPIPS runtime."""

import argparse
import contextlib
import hashlib
import json
import os
import sys
from importlib.metadata import version
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--candidate", type=Path)
    return parser


def _model():
    import lpips

    with contextlib.redirect_stdout(sys.stderr):
        return lpips.LPIPS(net="alex", version="0.1", verbose=False).eval()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tensor(path: Path, *, size: tuple[int, int] | None = None):
    import numpy
    import torch
    from PIL import Image, ImageOps

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        if size is not None:
            image = image.resize(size, Image.Resampling.LANCZOS)
        array = numpy.asarray(image, dtype=numpy.float32)
    normalized = array / 127.5 - 1.0
    return torch.from_numpy(normalized.transpose(2, 0, 1)).unsqueeze(0)


def main() -> int:
    args = _parser().parse_args()
    if args.health:
        if args.reference is not None or args.candidate is not None:
            raise SystemExit("--health cannot be combined with image inputs")
        import lpips
        import torch
        import torchvision

        _model()
        alexnet_path = Path(os.environ["TORCH_HOME"]) / "hub/checkpoints/alexnet-owt-7be5be79.pth"
        lpips_alex_path = Path(lpips.__file__).parent / "weights/v0.1/alex.pth"
        print(
            json.dumps(
                {
                    "ok": True,
                    "metric": "lpips",
                    "version": "0.1",
                    "package_version": version("lpips"),
                    "network": "alex",
                    "torch_version": torch.__version__,
                    "torchvision_version": torchvision.__version__,
                    "alexnet_sha256": _sha256(alexnet_path),
                    "lpips_alex_sha256": _sha256(lpips_alex_path),
                },
                sort_keys=True,
            )
        )
        return 0
    if args.reference is None or args.candidate is None:
        raise SystemExit("--reference and --candidate are required")
    from PIL import Image

    with Image.open(args.candidate) as candidate_source:
        candidate_size = candidate_source.size
    reference = _tensor(args.reference, size=candidate_size)
    candidate = _tensor(args.candidate)
    model = _model()
    import torch

    with torch.inference_mode():
        distance = float(model(reference, candidate).item())
    print(
        json.dumps(
            {"version": 1, "metric": "lpips", "network": "alex", "distance": distance},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
