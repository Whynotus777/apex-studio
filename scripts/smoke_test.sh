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

echo "[apex] Seeding demo data..."
"$(python_bin)" "$ROOT_DIR/scripts/seed_demo.py"

echo "[apex] Compiling Python sources..."
"$(python_bin)" -m py_compile "$ROOT_DIR/api/main.py" "$ROOT_DIR/scripts/seed_demo.py"

echo "[apex] Verifying FastAPI import..."
PYTHONPATH="$ROOT_DIR" APEX_HOME="$ROOT_DIR" "$(python_bin)" - <<'PY'
from api.main import app
print("[apex] FastAPI import OK:", app.title)
PY

# ── Frontend build check ──────────────────────────────────────────────────────
if command -v node >/dev/null 2>&1 && [ -d "$ROOT_DIR/ui/web" ]; then
  echo "[apex] Checking Next.js build..."
  if (cd "$ROOT_DIR/ui/web" && npm run build 2>&1 | tail -5); then
    echo "[apex] Next.js build: OK"
  else
    echo "[apex] Next.js build: FAILED" >&2
    exit 1
  fi
else
  echo "[apex] Skipping Next.js build check (node not found or ui/web missing)"
fi

# ── Live service tests ────────────────────────────────────────────────────────
if port_in_use 8000 && port_in_use 3000; then
  echo "[apex] Both services running. Running e2e tests..."
  PYTHONPATH="$ROOT_DIR" "$(python_bin)" "$ROOT_DIR/scripts/e2e_test.py"
  echo "[apex] Running health checks..."
  bash "$ROOT_DIR/scripts/check_health.sh"
elif port_in_use 8000; then
  echo "[apex] API running (port 8000). Running e2e tests..."
  PYTHONPATH="$ROOT_DIR" "$(python_bin)" "$ROOT_DIR/scripts/e2e_test.py"
  echo "[apex] Frontend not running (port 3000) — skipped browser tests"
else
  echo "[apex] Services not running; skipped live HTTP tests."
  echo "[apex] To run full suite: make dev  (then re-run this script)"
fi

echo "[apex] Smoke test complete."
