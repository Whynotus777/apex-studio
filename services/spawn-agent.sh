#!/bin/bash
# spawn-agent.sh — Boot an APEX agent with context injection
# Usage: ./services/spawn-agent.sh <agent_name> [task_id]
set -e

APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
DB="$APEX_HOME/db/apex_state.db"
AGENT_NAME="${1:?Usage: spawn-agent.sh <agent_name> [task_id]}"
TASK_ID="${2:-}"
AGENT_DIR="$APEX_HOME/agents/$AGENT_NAME"
WORKSPACE="$APEX_HOME/workspace"
TMP_DIR=$(mktemp -d)

if [ ! -d "$AGENT_DIR" ]; then
  echo "ERROR: Agent '$AGENT_NAME' not found" >&2
  exit 1
fi

[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [spawn:$AGENT_NAME] $*"; }
db_query() { sqlite3 -separator '|' "$DB" "$1"; }
cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

get_json_field() {
  python3 -c "
import json
c = json.load(open('$AGENT_DIR/agent.json'))
keys = '$1'.split('.')
v = c
for k in keys:
    if isinstance(v, dict): v = v.get(k, '')
    else: v = ''; break
print(v)
" 2>/dev/null
}

###############################################################################
# 1. UPDATE STATUS
###############################################################################

log "Spawning agent: $AGENT_NAME"
SESSION_ID="sess-${AGENT_NAME}-$(date +%s)"

db_query "UPDATE agent_status SET status='active', last_heartbeat=datetime('now'),
  session_id='$SESSION_ID' WHERE agent_name='$AGENT_NAME';"

###############################################################################
# 2. BUILD SLIM PROMPTS (optimized for 4B models)
###############################################################################

# Read hard rules only (skip soft prefs and anti-patterns for 4B)
HARD_RULES=""
[ -f "$AGENT_DIR/constraints/hard-rules.md" ] && HARD_RULES=$(cat "$AGENT_DIR/constraints/hard-rules.md")

# Get agent role from agent.json
ROLE=$(get_json_field "role")
DESC=$(get_json_field "description")

# System prompt: ~200 tokens max
cat > "$TMP_DIR/system_prompt.txt" << SYSEOF
You are $AGENT_NAME, the $ROLE agent in APEX venture studio.
Job: $DESC

Hard rules:
$HARD_RULES

Respond concisely. No preamble. Structure your response as:
ACTIONS TAKEN: (only actions you actually performed — never claim work you did not do)
OBSERVATIONS: (what you noticed about your context, inbox, task)
PROPOSED OUTPUT: (your deliverable, clearly labeled as PROPOSED if not yet executed)
MESSAGES: (TO:<agent> | TYPE:<type> | CONTENT:<msg>) or "none" — valid agents: apex, scout, analyst, builder, critic
SCRATCHPAD UPDATE: (key facts to remember)
STATUS: done | blocked:<reason> | needs_review:<low|medium|high>
SYSEOF

# User prompt: task + inbox only
{
  # Inbox
  INBOX=$(db_query "SELECT from_agent, content FROM agent_messages
    WHERE to_agent='$AGENT_NAME' AND status='pending'
    ORDER BY priority ASC LIMIT 5;")
  if [ -n "$INBOX" ]; then
    echo "Inbox:"
    echo "$INBOX"
    echo ""
  fi

  db_query "UPDATE agent_messages SET status='read'
    WHERE to_agent='$AGENT_NAME' AND status='pending';"

  # Task context (slim: just title, description, goal)
  if [ -n "$TASK_ID" ]; then
    TASK_INFO=$(db_query "SELECT t.title, t.description, g.name
      FROM tasks t
      LEFT JOIN goals g ON t.goal_id = g.id
      WHERE t.id='$TASK_ID';")

    if [ -n "$TASK_INFO" ]; then
      ALREADY_CHECKED=$(db_query "SELECT checked_out_by FROM tasks
        WHERE id='$TASK_ID' AND checked_out_by IS NOT NULL AND checked_out_by != '$AGENT_NAME';")
      if [ -n "$ALREADY_CHECKED" ]; then
        log "ERROR: Task $TASK_ID checked out by $ALREADY_CHECKED"
        db_query "UPDATE agent_status SET status='idle' WHERE agent_name='$AGENT_NAME';"
        exit 1
      fi
      db_query "UPDATE tasks SET checked_out_by='$AGENT_NAME', checked_out_at=datetime('now'),
        status=CASE WHEN status='backlog' THEN 'in_progress' ELSE status END
        WHERE id='$TASK_ID';"

      echo "Task: $TASK_ID"
      echo "$TASK_INFO"
    fi
  else
    echo "No specific task. Run your heartbeat responsibilities."
  fi
} > "$TMP_DIR/user_prompt.txt"

###############################################################################
# 3. CALL MODEL
###############################################################################

MODEL_PRIMARY=$(get_json_field "model.primary")
TEMPERATURE=$(get_json_field "api_config.temperature")

log "Calling model: $MODEL_PRIMARY (prompt: $(wc -c < "$TMP_DIR/system_prompt.txt") + $(wc -c < "$TMP_DIR/user_prompt.txt") bytes)"

RESPONSE=$(python3 "$APEX_HOME/services/call_model.py" \
  "$MODEL_PRIMARY" \
  "$TMP_DIR/system_prompt.txt" \
  "$TMP_DIR/user_prompt.txt" \
  "${TEMPERATURE:-0.3}" 2>/dev/null) || true

# Fallback chain
if [ -z "$RESPONSE" ]; then
  FALLBACK=$(get_json_field "model.fallback")
  log "Primary failed, trying fallback: $FALLBACK"

  if echo "$FALLBACK" | grep -q "claude"; then
    if [ -n "$ANTHROPIC_API_KEY" ]; then
      RESPONSE=$(python3 "$APEX_HOME/services/call_model.py" "$FALLBACK" \
        "$TMP_DIR/system_prompt.txt" "$TMP_DIR/user_prompt.txt" \
        "${TEMPERATURE:-0.3}" 2>/dev/null) || true
    else
      log "No API key, skipping Claude fallback"
    fi
  fi
fi

if [ -z "$RESPONSE" ]; then
  log "All models failed, trying qwen3.5-apex as last resort"
  RESPONSE=$(python3 "$APEX_HOME/services/call_model.py" "qwen3.5-apex" \
    "$TMP_DIR/system_prompt.txt" "$TMP_DIR/user_prompt.txt" "0.3" 2>/dev/null) || true
fi

if [ -z "$RESPONSE" ]; then
  RESPONSE="ERROR: All model calls failed. Check Ollama is running (ollama ps)."
fi

###############################################################################
# 4. PROCESS RESPONSE
###############################################################################

RESP_LENGTH=${#RESPONSE}
log "Response received ($RESP_LENGTH chars)"

# Save session
ESCAPED=$(echo "$RESPONSE" | head -200 | sed "s/'/''/g")
db_query "INSERT OR REPLACE INTO agent_sessions (id, agent_name, task_id, context, last_active, status)
  VALUES ('$SESSION_ID','$AGENT_NAME','$TASK_ID','$ESCAPED',datetime('now'),'active');"

# Append to scratchpad
SCRATCHPAD_ENTRY=$(echo "$RESPONSE" | sed -n '/SCRATCHPAD UPDATE/,/^STATUS:/p' | head -10)
if [ -n "$SCRATCHPAD_ENTRY" ]; then
  {
    echo ""
    echo "--- $SESSION_ID | $(date '+%Y-%m-%d %H:%M:%S') | Task: ${TASK_ID:-heartbeat} ---"
    echo "$SCRATCHPAD_ENTRY"
  } >> "$AGENT_DIR/workspace/scratchpad.md"
fi

# Inter-agent messages (allowlisted targets only)
VALID_AGENTS="apex scout analyst builder critic"
echo "$RESPONSE" | grep "^TO:" | while IFS= read -r msg_line; do
  TO_AGENT=$(echo "$msg_line" | sed -n 's/.*TO:\([^ |]*\).*/\1/p')
  MSG_CONTENT=$(echo "$msg_line" | sed -n 's/.*CONTENT:\(.*\)/\1/p')
  if [ -n "$TO_AGENT" ] && [ -n "$MSG_CONTENT" ]; then
    # Validate target agent
    if echo "$VALID_AGENTS" | grep -qw "$TO_AGENT"; then
      SAFE=$(echo "$MSG_CONTENT" | sed "s/'/''/g")
      db_query "INSERT INTO agent_messages (from_agent,to_agent,msg_type,content,task_id)
        VALUES ('$AGENT_NAME','$TO_AGENT','request','$SAFE','$TASK_ID');"
      log "Message sent: $AGENT_NAME → $TO_AGENT"
    else
      log "BLOCKED message to invalid agent: $TO_AGENT (rerouting to apex)"
      SAFE=$(echo "[$TO_AGENT] $MSG_CONTENT" | sed "s/'/''/g")
      db_query "INSERT INTO agent_messages (from_agent,to_agent,msg_type,content,task_id)
        VALUES ('$AGENT_NAME','apex','escalation','$SAFE','$TASK_ID');"
    fi
  fi
done

# Task status
if [ -n "$TASK_ID" ]; then
  STATUS_LINE=$(echo "$RESPONSE" | grep -i "^STATUS:" | tail -1 || echo "")
  case "$STATUS_LINE" in
    *needs_review*)
      STAKES=$(echo "$STATUS_LINE" | sed -n 's/.*needs_review:\([a-z]*\).*/\1/p')
      db_query "INSERT INTO reviews (task_id,agent_name,output_ref,stakes)
        VALUES ('$TASK_ID','$AGENT_NAME','$SESSION_ID','${STAKES:-low}');"
      db_query "UPDATE tasks SET review_status='pending', status='review' WHERE id='$TASK_ID';"
      log "Review queued: $TASK_ID ($STAKES)"
      ;;
    *done*)
      db_query "UPDATE tasks SET status='done', completed_at=datetime('now'),
        checked_out_by=NULL WHERE id='$TASK_ID';"
      log "Task completed: $TASK_ID"
      ;;
    *blocked*)
      db_query "UPDATE tasks SET status='blocked' WHERE id='$TASK_ID';"
      log "Task blocked: $TASK_ID"
      ;;
    *)
      db_query "UPDATE tasks SET checked_out_by=NULL WHERE id='$TASK_ID';"
      ;;
  esac
fi

db_query "UPDATE agent_status SET status='idle', current_task=NULL,
  last_heartbeat=datetime('now') WHERE agent_name='$AGENT_NAME';"

###############################################################################
# 5. OUTPUT
###############################################################################

echo ""
echo "============================================"
echo "  APEX Agent: $AGENT_NAME"
echo "  Session: $SESSION_ID"
echo "  Task: ${TASK_ID:-heartbeat}"
echo "============================================"
echo ""
echo "$RESPONSE"

log "Session complete."
