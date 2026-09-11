#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v uv >/dev/null || { echo 'Install uv from https://docs.astral.sh/uv/ before starting.'; exit 1; }
uv sync --locked
npm --prefix frontend ci
npm --prefix frontend run build
export DEMO_MODE=1
export PORT="${PORT:-5187}"
export PUBLIC_URL="${PUBLIC_URL:-http://127.0.0.1:$PORT}"
exec uv run --locked python -m backend
