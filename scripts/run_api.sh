#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "$0")" && pwd)/common.sh"

ensure_runtime_prereqs
ensure_venv
pip_install_backend_deps
load_env
ensure_db
apply_wal_mode
assert_port_free 8000 "API"

log "Starting FastAPI backend on http://localhost:8000"
cd "$ROOT_DIR"
PYTHONPATH="$ROOT_DIR" APEX_HOME="$ROOT_DIR" exec "$(python_bin)" -m uvicorn api.main:app --host 0.0.0.0 --port 8000
