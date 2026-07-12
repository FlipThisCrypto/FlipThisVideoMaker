#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p run

start_worker() {
  local device="$1"
  local pid_file="run/worker-${device}.pid"
  if [[ -f "$pid_file" ]] && kill -0 "$(<"$pid_file")" 2>/dev/null; then
    printf '%s worker is already running.\n' "$device"
    return
  fi
  nohup uv run flipthis-worker --device "$device" >"run/worker-${device}.log" 2>&1 &
  printf '%s' "$!" >"$pid_file"
  printf 'Started %s worker (PID %s).\n' "$device" "$!"
}

if [[ "${1:-cpu}" == "all" ]]; then
  start_worker cpu
  start_worker gpu0
  start_worker gpu1
else
  start_worker "${1:-cpu}"
fi
