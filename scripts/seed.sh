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
exec "$(python_bin)" "$ROOT_DIR/scripts/seed_demo.py"
