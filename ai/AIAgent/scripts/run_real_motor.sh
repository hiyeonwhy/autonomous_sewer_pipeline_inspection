#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "Missing ai/AIAgent/.env; real-motor runtime cannot start." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

: "${PIPECHECKKK_EDGE_URL:?PIPECHECKKK_EDGE_URL is required}"
: "${PIPECHECKKK_EDGE_TOKEN:?PIPECHECKKK_EDGE_TOKEN is required}"
: "${PIPECHECKKK_CAMERA_SNAPSHOT_URL:?PIPECHECKKK_CAMERA_SNAPSHOT_URL is required}"

exec ../.venv/bin/python -m uvicorn pipecheckkk_ai.api:app \
  --host "${PIPECHECKKK_AI_HOST:-127.0.0.1}" \
  --port "${PIPECHECKKK_AI_PORT:-8000}"