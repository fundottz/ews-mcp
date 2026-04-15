#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PASSWORD_ENV_NAME="${EXCHANGE_PASSWORD_ENV_NAME:-EXCHANGE_PASSWORD}"
PASSWORD_SECRET_REF="${EXCHANGE_PASSWORD_SECRET_REF:-OPENCLAW_EXCHANGE_PASSWORD}"

exec "$SCRIPT_DIR/bws_exec_env.py" "$PASSWORD_ENV_NAME" "$PASSWORD_SECRET_REF" -- \
  uv --directory "$REPO_ROOT" run sync.py
