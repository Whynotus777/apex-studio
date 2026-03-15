#!/bin/bash
# spawn-agent.sh — Boot an APEX agent with context injection
# Usage: ./kernel/spawn-agent.sh <agent_name> [task_id]
set -e

APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
DB="$APEX_HOME/db/apex_state.db"
AGENT_NAME="${1:?Usage: spawn-agent.sh <agent_name> [task_id]}"
TASK_ID="${2:-}"
AGENT_DIR="$APEX_HOME/templates/startup-chief-of-staff/agents/$AGENT_NAME"
WORKSPACE="$APEX_HOME/templates/startup-chief-of-staff/workspace"
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
# 2. BUILD SLIM PROMPTS
###############################################################################

HARD_RULES=""
[ -f "$AGENT_DIR/constraints/hard-rules.md" ] && HARD_RULES=$(cat "$AGENT_DIR/constraints/hard-rules.md")

ROLE=$(get_json_field "role")
DESC=$(get_json_field "description")

cat > "$TMP_DIR/system_prompt.txt" << SYSEOF
You are $AGENT_NAME, the $ROLE agent in APEX venture studio.
Job: $DESC

Hard rules:
$HARD_RULES

Search Evidence grounding: If Search Evidence is present in the user message, you may ONLY cite URLs and facts that appear in the Search Evidence section. If Search Evidence is absent or empty, do not invent sources and state that no evidence was retrieved.

Respond with ONLY a valid JSON object. No text before or after the JSON. Use this exact schema:
{
  "actions_taken": "what you actually did (not what you would do)",
  "observations": "what you noticed about your context and task",
  "proposed_output": "your deliverable, clearly labeled as proposed if not executed",
  "messages": [
    {"to": "agent_name", "type": "request|alert|escalation", "content": "message"}
  ],
  "scratchpad_update": "key facts to remember",
  "status": "done|blocked:reason|needs_review:low|needs_review:medium|needs_review:high"
}

Valid message targets: apex, scout, analyst, builder, critic. No other targets allowed.
If no messages needed, use an empty array: "messages": []
SYSEOF

MEMORY_JSON=$(python3 "$APEX_HOME/kernel/memory_loader.py" load "$AGENT_NAME" 2>/dev/null) || MEMORY_JSON=""
SESSION_CONTEXT=""
WORKING_MEMORY=""
DURABLE_MEMORY=""

if [ -n "$MEMORY_JSON" ]; then
  SESSION_CONTEXT=$(echo "$MEMORY_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('session_context',''))" 2>/dev/null)
  WORKING_MEMORY=$(echo "$MEMORY_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('working_memory',''))" 2>/dev/null)
  DURABLE_MEMORY=$(echo "$MEMORY_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('durable_memory',''))" 2>/dev/null)
fi

{
  if [ -n "$SESSION_CONTEXT" ]; then
    echo "Latest session context:"
    echo "$SESSION_CONTEXT"
    echo ""
  fi

  if [ -n "$WORKING_MEMORY" ]; then
    echo "Working memory:"
    echo "$WORKING_MEMORY"
    echo ""
  fi

  if [ -n "$DURABLE_MEMORY" ]; then
    echo "Durable memory:"
    echo "$DURABLE_MEMORY"
    echo ""
  fi

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

  # Search evidence injection
  if [ -n "$TASK_ID" ]; then
    cat > "$TMP_DIR/search_evidence.py" << 'PYEOF'
import sys, os
apex_home = os.environ['APEX_HOME']
db_path = os.environ['APEX_DB']
agent_name = os.environ['APEX_AGENT']
task_id = os.environ['APEX_TASK']
sys.path.insert(0, apex_home)
try:
    from kernel.api import ApexKernel
    k = ApexKernel()
    tools = k.get_agent_tools(agent_name)
    has_search = any(t.get('tool_id') == 'web_search' or t.get('name') == 'web_search' for t in tools)
    if not has_search:
        print('## Search Evidence\n(none available)')
        sys.exit(0)
    import sqlite3
    conn = sqlite3.connect(db_path)
    row = conn.execute('SELECT title, description FROM tasks WHERE id=?', (task_id,)).fetchone()
    conn.close()
    if not row or not row[0]:
        print('## Search Evidence\n(none available)')
        sys.exit(0)
    title, description = row
    query = (title + ' ' + (description or '')).strip()[:200]
    from kernel.tool_adapter import execute_tool
    result = execute_tool('web_search', {'query': query, 'max_results': 5})
    if result.get('status') != 'ok' or not result.get('results'):
        print('## Search Evidence\n(none available)')
        sys.exit(0)
    from kernel.evidence import EvidenceStore
    ev = EvidenceStore(db_path)
    ev.store_evidence(task_id, agent_name, 'web_search', query, result['results'])
    print(ev.format_for_prompt(task_id))
except Exception:
    print('## Search Evidence\n(none available)')
PYEOF
    SEARCH_EVIDENCE=$(APEX_HOME="$APEX_HOME" APEX_DB="$DB" APEX_AGENT="$AGENT_NAME" APEX_TASK="$TASK_ID" \
      python3 "$TMP_DIR/search_evidence.py" 2>/dev/null) \
      || SEARCH_EVIDENCE="## Search Evidence\n(none available)"
    echo ""
    echo "$SEARCH_EVIDENCE"
  fi
} > "$TMP_DIR/user_prompt.txt"

###############################################################################
# 3. CALL MODEL
###############################################################################

MODEL_PRIMARY=$(get_json_field "model.primary")
TEMPERATURE=$(get_json_field "api_config.temperature")

log "Calling model: $MODEL_PRIMARY (prompt: $(wc -c < "$TMP_DIR/system_prompt.txt") + $(wc -c < "$TMP_DIR/user_prompt.txt") bytes)"

RESPONSE=$(python3 "$APEX_HOME/kernel/call_model.py" \
  "$MODEL_PRIMARY" \
  "$TMP_DIR/system_prompt.txt" \
  "$TMP_DIR/user_prompt.txt" \
  "${TEMPERATURE:-0.3}" 2>/dev/null) || true

if [ -z "$RESPONSE" ]; then
  FALLBACK=$(get_json_field "model.fallback")
  log "Primary failed, trying fallback: $FALLBACK"
  if echo "$FALLBACK" | grep -q "claude"; then
    if [ -n "$ANTHROPIC_API_KEY" ]; then
      RESPONSE=$(python3 "$APEX_HOME/kernel/call_model.py" "$FALLBACK" \
        "$TMP_DIR/system_prompt.txt" "$TMP_DIR/user_prompt.txt" \
        "${TEMPERATURE:-0.3}" 2>/dev/null) || true
    else
      log "No API key, skipping Claude fallback"
    fi
  fi
fi

if [ -z "$RESPONSE" ]; then
  log "All models failed, trying qwen3.5-apex as last resort"
  RESPONSE=$(python3 "$APEX_HOME/kernel/call_model.py" "qwen3.5-apex" \
    "$TMP_DIR/system_prompt.txt" "$TMP_DIR/user_prompt.txt" "0.3" 2>/dev/null) || true
fi

if [ -z "$RESPONSE" ]; then
  RESPONSE='{"actions_taken":"none","observations":"All model calls failed","proposed_output":"none","messages":[],"scratchpad_update":"Model call failure","status":"blocked:model_failure"}'
fi

###############################################################################
# 4. PARSE RESPONSE (structured JSON parser)
###############################################################################

RESP_LENGTH=${#RESPONSE}
log "Response received ($RESP_LENGTH chars)"

# Save raw response
echo "$RESPONSE" > "$TMP_DIR/raw_response.txt"

# Parse through the structured parser
PARSED=$(python3 "$APEX_HOME/kernel/parse_response.py" "$TMP_DIR/raw_response.txt" 2>/dev/null) || PARSED=""

if [ -z "$PARSED" ]; then
  log "WARN: Parser failed, storing raw response"
  PARSED="{\"actions_taken\":\"parse_error\",\"observations\":\"Parser could not process response\",\"proposed_output\":\"\",\"messages\":[],\"scratchpad_update\":\"\",\"status\":{\"state\":\"unknown\",\"reason\":\"parse_error\"},\"_parse_method\":\"error\"}"
fi

# Extract fields using Python (safe, no grep/sed)
PARSE_METHOD=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('_parse_method','unknown'))" 2>/dev/null)
log "Parse method: $PARSE_METHOD"

SCRATCHPAD=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('scratchpad_update',''))" 2>/dev/null)
MEMORY_SESSION_CONTEXT="$RESPONSE" APEX_TASK_ID="$TASK_ID" python3 "$APEX_HOME/kernel/memory_loader.py" \
  save "$AGENT_NAME" "$SESSION_ID" "$SCRATCHPAD" 2>/dev/null || true

# Process messages via parser (handles allowlist internally)
echo "$PARSED" | python3 -c "
import json, sys, sqlite3, os

parsed = json.load(sys.stdin)
messages = parsed.get('messages', [])
agent = '$AGENT_NAME'
task_id = '$TASK_ID'
db_path = os.path.join('$APEX_HOME', 'db', 'apex_state.db')

if not messages:
    sys.exit(0)

conn = sqlite3.connect(db_path)
cur = conn.cursor()
for msg in messages:
    to_agent = msg.get('to', '')
    msg_type = msg.get('type', 'request')
    content = msg.get('content', '')
    if to_agent and content:
        cur.execute(
            'INSERT INTO agent_messages (from_agent, to_agent, msg_type, content, task_id) VALUES (?,?,?,?,?)',
            (agent, to_agent, msg_type, content, task_id)
        )
        print(f'  Message: {agent} → {to_agent} ({msg_type})')
conn.commit()
conn.close()
" 2>/dev/null && true

# Handle task status
if [ -n "$TASK_ID" ]; then
  STATUS_STATE=$(echo "$PARSED" | python3 -c "import json,sys; s=json.load(sys.stdin).get('status',{}); print(s.get('state','') if isinstance(s,dict) else s)" 2>/dev/null)

  case "$STATUS_STATE" in
    needs_review)
      STAKES=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',{}).get('stakes','low'))" 2>/dev/null)
      db_query "INSERT INTO reviews (task_id,agent_name,output_ref,stakes)
        VALUES ('$TASK_ID','$AGENT_NAME','$SESSION_ID','${STAKES:-low}');"
      db_query "UPDATE tasks SET review_status='pending', status='review' WHERE id='$TASK_ID';"
      log "Review queued: $TASK_ID ($STAKES)"
      ;;
    done)
      db_query "UPDATE tasks SET status='done', completed_at=datetime('now'),
        checked_out_by=NULL WHERE id='$TASK_ID';"
      log "Task completed: $TASK_ID"
      ;;
    blocked)
      db_query "UPDATE tasks SET status='blocked' WHERE id='$TASK_ID';"
      REASON=$(echo "$PARSED" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status',{}).get('reason',''))" 2>/dev/null)
      log "Task blocked: $TASK_ID — $REASON"
      ;;
    *)
      db_query "UPDATE tasks SET checked_out_by=NULL WHERE id='$TASK_ID';"
      log "Status unclear: $STATUS_STATE — releasing checkout"
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
echo "  Parse: $PARSE_METHOD"
echo "============================================"
echo ""

# Pretty-print the parsed response
echo "$PARSED" | python3 -c "
import json, sys
p = json.load(sys.stdin)
print(f\"ACTIONS: {p.get('actions_taken', 'none')}\")
print(f\"OBSERVATIONS: {p.get('observations', 'none')}\")
print(f\"PROPOSED OUTPUT: {p.get('proposed_output', 'none')}\")
msgs = p.get('messages', [])
if msgs:
    print(f'MESSAGES ({len(msgs)}):')
    for m in msgs:
        print(f'  → {m[\"to\"]}: [{m[\"type\"]}] {m[\"content\"]}')
else:
    print('MESSAGES: none')
print(f\"SCRATCHPAD: {p.get('scratchpad_update', 'none')}\")
s = p.get('status', {})
if isinstance(s, dict):
    print(f\"STATUS: {s.get('state', 'unknown')} {s.get('reason', '') or s.get('stakes', '')}\")
else:
    print(f'STATUS: {s}')
" 2>/dev/null

log "Session complete."
