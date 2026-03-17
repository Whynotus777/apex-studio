#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
LOG_DIR="$ROOT_DIR/.apex-dev"
API_LOG="$LOG_DIR/api.log"
API_PID_FILE="$LOG_DIR/api.pid"
TAIL_PID_FILE="$LOG_DIR/api-tail.pid"
WEB_PID_FILE="$LOG_DIR/web.pid"
DEFAULT_API_URL="http://localhost:8000"
DEFAULT_APEX_HOME="$ROOT_DIR"

log() {
  printf '[apex] %s\n' "$*"
}

warn() {
  printf '[apex] warning: %s\n' "$*" >&2
}

die() {
  printf '[apex] error: %s\n' "$*" >&2
  exit 1
}

ensure_commands() {
  command -v python3 >/dev/null 2>&1 || die "Python 3.10+ is required but 'python3' was not found."
  command -v node >/dev/null 2>&1 || die "Node.js 18+ is required but 'node' was not found."
  command -v npm >/dev/null 2>&1 || die "npm is required but was not found. Install Node.js 18+."
  command -v curl >/dev/null 2>&1 || die "curl is required but was not found."
}

check_python_version() {
  python3 - <<'PY' || exit 1
import sys
if sys.version_info < (3, 10):
    print("[apex] error: Python 3.10+ is required. Found %s" % sys.version.split()[0], file=sys.stderr)
    raise SystemExit(1)
print("[apex] Python", sys.version.split()[0])
PY
}

check_node_version() {
  node - <<'JS' || exit 1
const major = Number(process.versions.node.split('.')[0]);
if (major < 18) {
  console.error(`[apex] error: Node.js 18+ is required. Found ${process.versions.node}`);
  process.exit(1);
}
console.log(`[apex] Node ${process.versions.node}`);
JS
}

ensure_runtime_prereqs() {
  ensure_commands
  check_python_version
  check_node_version
}

ensure_venv() {
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    log "Creating virtual environment at .venv/"
    python3 -m venv "$VENV_DIR"
  fi
}

python_bin() {
  printf '%s\n' "$VENV_DIR/bin/python"
}

pip_install_backend_deps() {
  local py
  py="$(python_bin)"
  log "Installing Python dependencies"
  "$py" -m pip install --upgrade pip setuptools wheel >/dev/null
  "$py" -m pip install -r "$ROOT_DIR/api/requirements.txt"
}

npm_install_web_deps() {
  log "Installing Node dependencies"
  (
    cd "$ROOT_DIR/ui/web"
    npm install
  )
}

ensure_env_file() {
  if [[ ! -f "$ROOT_DIR/.env" ]]; then
    if [[ -f "$ROOT_DIR/.env.example" ]]; then
      cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
      warn ".env was missing. Copied defaults from .env.example; review values before using external integrations."
    else
      cat > "$ROOT_DIR/.env" <<ENV
ANTHROPIC_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
OLLAMA_URL=http://localhost:11434
APEX_HOME=$DEFAULT_APEX_HOME
NEXT_PUBLIC_API_URL=$DEFAULT_API_URL
ENV
      warn ".env and .env.example were missing. Created a minimal .env file."
    fi
  fi

  grep -q '^APEX_HOME=' "$ROOT_DIR/.env" || printf 'APEX_HOME=%s\n' "$DEFAULT_APEX_HOME" >> "$ROOT_DIR/.env"
  grep -q '^NEXT_PUBLIC_API_URL=' "$ROOT_DIR/.env" || printf 'NEXT_PUBLIC_API_URL=%s\n' "$DEFAULT_API_URL" >> "$ROOT_DIR/.env"
}

load_env() {
  ensure_env_file
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
  export APEX_HOME="${APEX_HOME:-$DEFAULT_APEX_HOME}"
  export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-$DEFAULT_API_URL}"
}

ensure_db() {
  local py
  py="$(python_bin 2>/dev/null || true)"
  if [[ -z "$py" || ! -x "$py" ]]; then
    py="python3"
  fi

  if [[ ! -f "$ROOT_DIR/db/apex_state.db" ]]; then
    log "Initializing SQLite database from db/schema.sql"
    "$py" - "$ROOT_DIR" <<'PY'
import sqlite3
import sys
from pathlib import Path
root = Path(sys.argv[1])
db_path = root / "db" / "apex_state.db"
schema = (root / "db" / "schema.sql").read_text()
conn = sqlite3.connect(db_path)
try:
    conn.executescript(schema)
    conn.commit()
finally:
    conn.close()
PY
  fi
}

apply_wal_mode() {
  local py
  py="$(python_bin 2>/dev/null || true)"
  if [[ -z "$py" || ! -x "$py" ]]; then
    py="python3"
  fi
  log "Applying SQLite WAL mode"
  "$py" - "$ROOT_DIR/db/apex_state.db" <<'PY'
import sqlite3
import sys
path = sys.argv[1]
conn = sqlite3.connect(path)
try:
    mode = conn.execute("PRAGMA journal_mode=WAL;").fetchone()[0]
    print(f"[apex] SQLite journal_mode={mode}")
finally:
    conn.close()
PY
}

port_in_use() {
  python3 - "$1" <<'PY'
import socket
import sys
port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.25)
    code = sock.connect_ex(("127.0.0.1", port))
raise SystemExit(0 if code == 0 else 1)
PY
}

assert_port_free() {
  local port="$1"
  local label="$2"
  if port_in_use "$port"; then
    die "$label port $port is already in use. Stop the existing process or change ports before running APEX."
  fi
}

wait_for_http() {
  local url="$1"
  local label="$2"
  local attempts="${3:-40}"
  local i
  for ((i=1; i<=attempts; i++)); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$label is up at $url"
      return 0
    fi
    sleep 0.5
  done
  return 1
}

start_api_bg() {
  mkdir -p "$LOG_DIR"
  : > "$API_LOG"
  assert_port_free 8000 "API"
  local py
  py="$(python_bin)"
  log "Starting FastAPI backend on port 8000"
  (
    cd "$ROOT_DIR"
    PYTHONPATH="$ROOT_DIR" APEX_HOME="$ROOT_DIR" "$py" -m uvicorn api.main:app --host 0.0.0.0 --port 8000
  ) >"$API_LOG" 2>&1 &
  local api_pid=$!
  printf '%s\n' "$api_pid" > "$API_PID_FILE"

  if ! wait_for_http "http://localhost:8000/docs" "API" 40; then
    cat "$API_LOG" >&2 || true
    kill "$api_pid" >/dev/null 2>&1 || true
    rm -f "$API_PID_FILE"
    die "FastAPI failed to start on port 8000. See log output above."
  fi

  tail -n +1 -f "$API_LOG" &
  printf '%s\n' "$!" > "$TAIL_PID_FILE"
}

start_web_fg() {
  assert_port_free 3000 "Web UI"
  log "Starting Next.js dev server on port 3000"
  (
    cd "$ROOT_DIR/ui/web"
    NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-$DEFAULT_API_URL}" npm run dev
  ) &
  local web_pid=$!
  printf '%s\n' "$web_pid" > "$WEB_PID_FILE"
  if ! wait_for_http "http://localhost:3000" "Web UI" 80; then
    kill "$web_pid" >/dev/null 2>&1 || true
    rm -f "$WEB_PID_FILE"
    die "Next.js failed to start on port 3000."
  fi
  printf 'APEX is running at http://localhost:3000 — API at http://localhost:8000\n'
  wait "$web_pid"
}

kill_from_pid_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    local pid
    pid="$(cat "$file" 2>/dev/null || true)"
    if [[ -n "$pid" ]]; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$file"
  fi
}

cleanup_dev_processes() {
  kill_from_pid_file "$WEB_PID_FILE"
  kill_from_pid_file "$TAIL_PID_FILE"
  kill_from_pid_file "$API_PID_FILE"
}
