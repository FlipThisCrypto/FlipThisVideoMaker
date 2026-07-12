#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
shopt -s nullglob
for pid_file in run/*.pid; do
  pid="$(<"$pid_file")"
  name="$(basename "$pid_file" .pid)"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    for _ in {1..50}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid"
    printf 'Stopped %s.\n' "$name"
  fi
  rm -f "$pid_file"
done
