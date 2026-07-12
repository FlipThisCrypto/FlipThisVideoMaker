#!/usr/bin/env bash
set -euo pipefail

uv sync --extra dev
uv run alembic upgrade head
COREPACK_ENABLE_DOWNLOAD_PROMPT=0 corepack enable
COREPACK_ENABLE_DOWNLOAD_PROMPT=0 pnpm install --frozen-lockfile
