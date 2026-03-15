#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

echo "=== APEX JSON Output Test ==="
echo ""

# Run parser unit tests first
"$APEX_HOME/tests/test_parser.sh"
echo ""

# Live agent test
echo "--- Live Agent Test (Builder, ~1-2 min) ---"
sqlite3 "$APEX_HOME/db/apex_state.db" "UPDATE tasks SET status='backlog', checked_out_by=NULL WHERE id='test-json-001';"
"$APEX_HOME/kernel/spawn-agent.sh" builder test-json-001

echo ""
echo "--- Post-run DB check ---"
echo "Task:"
sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT id, status, review_status FROM tasks WHERE id='test-json-001';"
echo "Messages:"
sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT from_agent, to_agent, msg_type FROM agent_messages ORDER BY id DESC LIMIT 3;"
