#!/usr/bin/env bash
set -euo pipefail

device="${1:-cpu}"
exec uv run flipthis-worker --device "$device"
