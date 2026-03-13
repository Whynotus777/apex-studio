#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

echo "=== APEX Critic Pipeline Test ==="
echo ""

# Step 1: Create a task and have Builder work on it
echo "--- Step 1: Builder produces output ---"
sqlite3 "$APEX_HOME/db/apex_state.db" "DELETE FROM tasks WHERE id IN ('critic-test-001','critic-test-002');"
sqlite3 "$APEX_HOME/db/apex_state.db" "DELETE FROM reviews WHERE task_id IN ('critic-test-001','critic-test-002');"
sqlite3 "$APEX_HOME/db/apex_state.db" "DELETE FROM evals WHERE task_id IN ('critic-test-001','critic-test-002');"

# Good task (should PASS)
sqlite3 "$APEX_HOME/db/apex_state.db" "INSERT INTO tasks
  (id, project_id, goal_id, title, description, pipeline_stage, assigned_to, status)
  VALUES ('critic-test-001', 'proj-meridian-core', 'goal-meridian',
  'Diagnostic: report your context for Critic review',
  'Report your agent name, task id, and whether hard rules are visible. Do not write code. Do not invent files. Submit for review when done.',
  'build', 'builder', 'backlog');"

echo "  Spawning Builder on critic-test-001..."
"$APEX_HOME/services/spawn-agent.sh" builder critic-test-001

echo ""
echo "--- Step 2: Check review queue ---"
echo "Reviews pending:"
sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT id, task_id, agent_name, stakes, verdict FROM reviews WHERE task_id='critic-test-001';"

# Manually queue a review if Builder marked it as done instead of needs_review
REVIEW_EXISTS=$(sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT COUNT(*) FROM reviews WHERE task_id='critic-test-001' AND verdict IS NULL;")
if [ "$REVIEW_EXISTS" = "0" ]; then
  echo "  No pending review found — manually queuing one for testing"
  LATEST_SESSION=$(sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT id FROM agent_sessions WHERE agent_name='builder' ORDER BY last_active DESC LIMIT 1;")
  sqlite3 "$APEX_HOME/db/apex_state.db" "INSERT INTO reviews (task_id, agent_name, output_ref, stakes)
    VALUES ('critic-test-001', 'builder', '$LATEST_SESSION', 'low');"
  sqlite3 "$APEX_HOME/db/apex_state.db" "UPDATE tasks SET status='review', review_status='pending' WHERE id='critic-test-001';"
fi

echo ""
echo "--- Step 3: Run Critic pipeline ---"
python3 "$APEX_HOME/services/run_critic.py"

echo ""
echo "--- Step 4: Post-Critic state ---"
echo ""
echo "Task status:"
sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT id, status, review_status FROM tasks WHERE id='critic-test-001';"
echo ""
echo "Review verdict:"
sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT id, task_id, stakes, verdict, reviewed_at FROM reviews WHERE task_id='critic-test-001';"
echo ""
echo "Eval scores:"
sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT dimension, score, max_score FROM evals WHERE task_id='critic-test-001';"
echo ""
echo "Messages from Critic:"
sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT from_agent, to_agent, msg_type, substr(content,1,100) FROM agent_messages WHERE from_agent='critic' ORDER BY id DESC LIMIT 3;"

echo ""
echo "=== Critic Pipeline Test Complete ==="
