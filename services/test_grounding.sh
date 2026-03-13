#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

echo "=== APEX Grounding Tests ==="
echo ""

# Test Scout
echo "--- Scout Grounding Test ---"
sqlite3 "$APEX_HOME/db/apex_state.db" "UPDATE tasks SET status='backlog', checked_out_by=NULL WHERE id='test-scout-001';"
"$APEX_HOME/services/spawn-agent.sh" scout test-scout-001

echo ""
echo "--- Analyst Grounding Test ---"
sqlite3 "$APEX_HOME/db/apex_state.db" "UPDATE tasks SET status='backlog', checked_out_by=NULL WHERE id='test-analyst-001';"
"$APEX_HOME/services/spawn-agent.sh" analyst test-analyst-001

echo ""
echo "=== Grounding Tests Complete ==="
echo ""
echo "Check: Did either agent invent URLs, stats, or claim to scan platforms?"
echo "If yes, grounding rules need further tightening."
