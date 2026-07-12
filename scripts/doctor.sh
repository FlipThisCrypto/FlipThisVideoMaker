#!/usr/bin/env bash
set -uo pipefail

cd "$(dirname "$0")/.."
failures=0

required() {
  local command="$1"
  if command -v "$command" >/dev/null; then
    printf 'OK required %-12s %s\n' "$command" "$("$command" --version 2>&1 | head -n 1)"
  else
    printf 'FAIL required %s is missing\n' "$command"
    failures=$((failures + 1))
  fi
}

for command in python3 uv node corepack ffmpeg ffprobe; do required "$command"; done

if command -v nvidia-smi >/dev/null; then
  if gpu_report="$(nvidia-smi --query-gpu=index,name,driver_version,temperature.gpu,utilization.gpu,memory.total,memory.used,memory.free --format=csv,noheader,nounits 2>&1)"; then
    printf 'OK optional NVIDIA GPUs\n%s\n' "$gpu_report"
  else
    printf 'INFO optional NVIDIA tooling is unavailable: %s\n' "$gpu_report"
  fi
else
  printf 'INFO optional nvidia-smi is absent; CPU mode is available\n'
fi

if uv run alembic current 2>/dev/null | grep -q '(head)'; then
  printf 'OK database migration is at head\n'
else
  printf 'FAIL database is not migrated to head\n'
  failures=$((failures + 1))
fi

for path in config/providers.yaml config/render-profiles.yaml config/workers.yaml; do
  [[ -r "$path" ]] && printf 'OK config %s\n' "$path" || { printf 'FAIL config %s\n' "$path"; failures=$((failures + 1)); }
done

for path in projects data .cache; do
  mkdir -p "$path"
  [[ -w "$path" ]] && printf 'OK writable %s\n' "$path" || { printf 'FAIL not writable %s\n' "$path"; failures=$((failures + 1)); }
done

if curl --silent --fail --max-time 2 http://127.0.0.1:8000/api/v1/health >/dev/null; then
  printf 'OK running API health\n'
else
  printf 'INFO API is not currently reachable on 127.0.0.1:8000\n'
fi

uv run python -c 'import asyncio, json; from pathlib import Path; from flipthis_video_maker.providers.registry import provider_health_records; print("Provider health:", json.dumps(asyncio.run(provider_health_records(Path("config/providers.yaml")))))' || failures=$((failures + 1))

model_count="$(find models -type f 2>/dev/null | wc -l)"
printf 'INFO local model files: %s (models are installed separately)\n' "$model_count"
printf 'Doctor completed with %s required failure(s).\n' "$failures"
exit "$failures"
