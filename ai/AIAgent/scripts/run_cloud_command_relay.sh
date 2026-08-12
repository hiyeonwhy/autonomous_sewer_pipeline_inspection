#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "Missing ai/AIAgent/.env; Cloud command relay cannot start." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

: "${PIPECHECKKK_CLOUD_API_URL:?PIPECHECKKK_CLOUD_API_URL is required}"

exec ../.venv/bin/python scripts/cloud_command_relay.py
