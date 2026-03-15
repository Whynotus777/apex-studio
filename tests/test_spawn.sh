#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

echo "=== APEX Diagnostic Test ==="
echo ""
echo "Task: test-002 (context injection diagnostic)"
echo "Agent: builder"
echo ""

# Reset task status
sqlite3 "$APEX_HOME/db/apex_state.db" "UPDATE tasks SET status='backlog', checked_out_by=NULL WHERE id='test-002';"

"$APEX_HOME/kernel/spawn-agent.sh" builder test-002

echo ""
echo "=== Post-Run Checks ==="
echo ""
echo "Task status:"
sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT id, status, checked_out_by, review_status FROM tasks WHERE id='test-002';"
echo ""
echo "Session:"
sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT id, agent_name, task_id FROM agent_sessions ORDER BY last_active DESC LIMIT 1;"
echo ""
echo "Messages sent (should be none or only to valid agents):"
sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT from_agent, to_agent, content FROM agent_messages ORDER BY id DESC LIMIT 3;"
echo ""
echo "Scratchpad tail:"
tail -20 "$APEX_HOME/templates/startup-chief-of-staff/agents/builder/workspace/scratchpad.md"
