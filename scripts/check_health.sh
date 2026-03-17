#!/usr/bin/env bash
set -euo pipefail

API_URL="${1:-http://localhost:8000/docs}"
WEB_URL="${2:-http://localhost:3000}"

api_ok=0
web_ok=0

if curl -fsS "$API_URL" >/dev/null 2>&1; then
  echo "API: OK ($API_URL)"
  api_ok=1
else
  echo "API: FAIL ($API_URL)"
fi

if curl -fsS "$WEB_URL" >/dev/null 2>&1; then
  echo "Web: OK ($WEB_URL)"
  web_ok=1
else
  echo "Web: FAIL ($WEB_URL)"
fi

if [[ "$api_ok" -eq 1 && "$web_ok" -eq 1 ]]; then
  exit 0
fi
exit 1
