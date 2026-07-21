#!/usr/bin/env bash
set -euo pipefail

readonly TORCH_VERSION="2.13.0+cpu"
readonly TORCHVISION_VERSION="0.28.0+cpu"
readonly LPIPS_VERSION="0.1.4"
readonly ALEXNET_SHA="7be5be791159472b1fbf3c69796f7cb30dca7ad8466c2df70058c37116cdee02"
readonly LPIPS_ALEX_SHA="df73285e35b22355a2df87cdb6b70b343713b667eddbda73e1977e0c860835c0"

if [[ $# -ne 1 || "$1" != /* ]]; then
  echo "usage: $0 /absolute/external/runtime/root" >&2
  exit 2
fi

runtime_root="${1%/}"
mkdir -p "$runtime_root/cache"
python3.11 -m venv "$runtime_root/.venv"
python_bin="$runtime_root/.venv/bin/python"
"$python_bin" -m pip install --upgrade "pip==26.1.2"
"$python_bin" -m pip install \
  --index-url https://download.pytorch.org/whl/cpu \
  "torch==$TORCH_VERSION" \
  "torchvision==$TORCHVISION_VERSION" \
  "filelock==3.29.0" \
  "typing-extensions==4.15.0" \
  "sympy==1.14.0" \
  "networkx==3.6.1" \
  "jinja2==3.1.6" \
  "fsspec==2026.4.0" \
  "mpmath==1.3.0" \
  "MarkupSafe==3.0.3"
"$python_bin" -m pip install \
  "lpips==$LPIPS_VERSION" \
  "numpy==2.3.2" \
  "pillow==11.3.0" \
  "scipy==1.17.1" \
  "tqdm==4.69.0"

script_path="$(cd "$(dirname "$0")" && pwd)/lpips_metric.py"
TORCH_HOME="$runtime_root/cache" "$python_bin" "$script_path" --health
alexnet_path="$runtime_root/cache/hub/checkpoints/alexnet-owt-7be5be79.pth"
lpips_alex_path="$runtime_root/.venv/lib/python3.11/site-packages/lpips/weights/v0.1/alex.pth"
if [[ "$(sha256sum "$alexnet_path" | awk '{print $1}')" != "$ALEXNET_SHA" ]]; then
  echo "AlexNet backbone checksum mismatch" >&2
  exit 1
fi
if [[ "$(sha256sum "$lpips_alex_path" | awk '{print $1}')" != "$LPIPS_ALEX_SHA" ]]; then
  echo "LPIPS 0.1 AlexNet calibration checksum mismatch" >&2
  exit 1
fi
echo "Local LPIPS runtime verified at $runtime_root"
echo "Configure python=$python_bin, script=$script_path, cache_directory=$runtime_root/cache"
