#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p run

start_process() {
  local name="$1"
  shift
  if [[ -f "run/${name}.pid" ]] && kill -0 "$(<"run/${name}.pid")" 2>/dev/null; then
    printf '%s is already running.\n' "$name"
    return
  fi
  nohup "$@" >"run/${name}.log" 2>&1 &
  printf '%s' "$!" >"run/${name}.pid"
  printf 'Started %s (PID %s).\n' "$name" "$!"
}

start_process api uv run flipthis-api
start_process web corepack pnpm --dir web dev --host 127.0.0.1
"$PWD/scripts/start_workers.sh" cpu
