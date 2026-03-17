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
"$(python_bin)" "$ROOT_DIR/scripts/seed_demo.py"
"$(python_bin)" -m py_compile "$ROOT_DIR/api/main.py" "$ROOT_DIR/scripts/seed_demo.py"
PYTHONPATH="$ROOT_DIR" APEX_HOME="$ROOT_DIR" "$(python_bin)" - <<'PY'
from api.main import app
print("[apex] FastAPI import OK:", app.title)
PY

if port_in_use 8000 && port_in_use 3000; then
  bash "$ROOT_DIR/scripts/check_health.sh"
else
  echo "[apex] services not running; skipped live HTTP health checks"
fi
