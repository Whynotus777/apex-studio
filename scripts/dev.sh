#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "$0")" && pwd)/common.sh"

cleanup() {
  cleanup_dev_processes
}
trap cleanup EXIT INT TERM

ensure_runtime_prereqs
ensure_venv
pip_install_backend_deps
npm_install_web_deps
load_env
ensure_db
apply_wal_mode
"$(python_bin)" "$ROOT_DIR/scripts/seed_demo.py"
start_api_bg
start_web_fg
