#!/usr/bin/env bash
set -euo pipefail

# shellcheck disable=SC1091
source "$(cd "$(dirname "$0")" && pwd)/common.sh"

ensure_runtime_prereqs
load_env
assert_port_free 3000 "Web UI"

log "Starting Next.js dev server on http://localhost:3000"
cd "$ROOT_DIR/ui/web"
NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-$DEFAULT_API_URL}" exec npm run dev
