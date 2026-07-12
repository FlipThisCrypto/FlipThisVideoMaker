#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

for command in curl uv pnpm; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'Required command is missing: %s\n' "$command" >&2
    exit 1
  }
done

if curl --silent --fail http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
  printf 'Port 8000 already has a FlipThisVideoMaker API; stop it before isolated E2E.\n' >&2
  exit 1
fi
if curl --silent --fail http://127.0.0.1:5173 >/dev/null 2>&1; then
  printf 'Port 5173 is already in use; stop the web server before isolated E2E.\n' >&2
  exit 1
fi

mkdir -p output/playwright
runtime="$(mktemp -d "$root/output/playwright/runtime.XXXXXX")"
api_pid=""
web_pid=""
worker_pid=""

cleanup() {
  status="$?"
  trap - EXIT INT TERM
  for pid in "$worker_pid" "$web_pid" "$api_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$worker_pid" "$web_pid" "$api_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
  if [[ "$status" -eq 0 ]]; then
    rm -rf "$runtime"
  else
    printf 'E2E runtime logs preserved at %s\n' "$runtime" >&2
  fi
  exit "$status"
}
trap cleanup EXIT INT TERM

export FTVM_DATABASE_URL="sqlite:///$runtime/e2e.db"
export FTVM_DATA_DIR="$runtime/projects"
export FTVM_CACHE_DIR="$runtime/cache"

uv run alembic upgrade head >"$runtime/migrations.log" 2>&1
uv run flipthis-api >"$runtime/api.log" 2>&1 &
api_pid="$!"
pnpm --dir web dev --host 127.0.0.1 >"$runtime/web.log" 2>&1 &
web_pid="$!"

for _ in {1..120}; do
  if curl --silent --fail http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1 &&
    curl --silent --fail http://127.0.0.1:5173 >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
curl --silent --fail http://127.0.0.1:8000/api/v1/health >/dev/null
curl --silent --fail http://127.0.0.1:5173 >/dev/null

uv run flipthis-worker --device cpu >"$runtime/worker.log" 2>&1 &
worker_pid="$!"

pnpm --dir web exec playwright test --config playwright.config.ts
