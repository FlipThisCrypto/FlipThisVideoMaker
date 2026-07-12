#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ "$(uname -s)" != "Linux" ]]; then
  printf 'This installer targets Linux; continue manually with README.md.\n' >&2
  exit 1
fi

if [[ -r /etc/os-release ]]; then
  # shellcheck disable=SC1091
  source /etc/os-release
  printf 'Distribution: %s\n' "${PRETTY_NAME:-unknown}"
fi

for command in python3 uv node corepack ffmpeg ffprobe; do
  if ! command -v "$command" >/dev/null; then
    printf 'Required command is missing: %s\n' "$command" >&2
    exit 1
  fi
done

python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ is required"'
uv sync --extra dev
uv run alembic upgrade head
COREPACK_ENABLE_DOWNLOAD_PROMPT=0 corepack enable
COREPACK_ENABLE_DOWNLOAD_PROMPT=0 pnpm install --frozen-lockfile
mkdir -p data/cache data/models data/gpu-locks projects run
[[ -f .env ]] || cp .env.example .env

if command -v nvidia-smi >/dev/null; then
  if gpu_report="$(nvidia-smi --query-gpu=index,name,driver_version,memory.total --format=csv,noheader 2>&1)"; then
    printf '%s\n' "$gpu_report"
  else
    printf 'Optional NVIDIA tooling is installed but unavailable: %s\n' "$gpu_report"
  fi
else
  printf 'Optional NVIDIA tooling not found; CPU mock mode remains available.\n'
fi

printf 'Core installation complete. No model weights were downloaded.\n'
