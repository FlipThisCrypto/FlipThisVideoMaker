#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 || "$1" != /* || ! "$2" =~ ^gpu[01]$ || ! "$3" =~ ^[0-9]+$ ]]; then
  echo "usage: $0 /absolute/external/runtime/root gpu0|gpu1 port" >&2
  exit 2
fi

runtime_root="${1%/}"
gpu_index="${2#gpu}"
port="$3"
if (( port < 1024 || port > 65535 )); then
  echo "port must be between 1024 and 65535" >&2
  exit 2
fi

comfy_root="$runtime_root/comfyui"
python_bin="$comfy_root/.venv/bin/python"
if [[ ! -x "$python_bin" || ! -f "$comfy_root/main.py" ]]; then
  echo "runtime is not installed; run scripts/install-wan22-flf.sh first" >&2
  exit 1
fi

cd "$comfy_root"
exec env \
  CUDA_VISIBLE_DEVICES="$gpu_index" \
  PYTORCH_CUDA_ALLOC_CONF="expandable_segments:True" \
  "$python_bin" main.py \
  --listen 127.0.0.1 \
  --port "$port" \
  --novram \
  --disable-auto-launch \
  --preview-method none
