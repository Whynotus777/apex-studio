#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [[ -f "$ROOT_DIR/.apex-dev/web.pid" ]]; then
  kill "$(cat "$ROOT_DIR/.apex-dev/web.pid")" >/dev/null 2>&1 || true
fi
if [[ -f "$ROOT_DIR/.apex-dev/api-tail.pid" ]]; then
  kill "$(cat "$ROOT_DIR/.apex-dev/api-tail.pid")" >/dev/null 2>&1 || true
fi
if [[ -f "$ROOT_DIR/.apex-dev/api.pid" ]]; then
  kill "$(cat "$ROOT_DIR/.apex-dev/api.pid")" >/dev/null 2>&1 || true
fi

rm -rf "$ROOT_DIR/.apex-dev" "$ROOT_DIR/.venv" "$ROOT_DIR/ui/web/node_modules"
echo "[apex] Cleaned dev processes, .venv, and ui/web/node_modules"
