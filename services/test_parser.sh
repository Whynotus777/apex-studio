#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"

echo "=== Parser Unit Tests ==="
echo ""

# Test 1: Valid JSON input
echo '--- Test 1: Valid JSON ---'
echo '{
  "actions_taken": "Reviewed task context",
  "observations": "Task is a diagnostic check",
  "proposed_output": "All systems nominal",
  "messages": [{"to": "apex", "type": "status_update", "content": "Test complete"}],
  "scratchpad_update": "Parser test successful",
  "status": "done"
}' > /tmp/test_response.txt
python3 "$APEX_HOME/services/parse_response.py" /tmp/test_response.txt
echo ""

# Test 2: Text format (old-style)
echo '--- Test 2: Text fallback ---'
cat > /tmp/test_response.txt << 'EOF'
ACTIONS TAKEN: Reviewed the task
OBSERVATIONS: Context injection working
PROPOSED OUTPUT: Diagnostic report complete
MESSAGES: TO:apex | TYPE:alert | CONTENT:All good here
SCRATCHPAD UPDATE: Text parsing still works
STATUS: done
EOF
python3 "$APEX_HOME/services/parse_response.py" /tmp/test_response.txt
echo ""

# Test 3: Invalid message target
echo '--- Test 3: Invalid message target ---'
echo '{
  "actions_taken": "none",
  "observations": "test",
  "proposed_output": "none",
  "messages": [{"to": "github-actions", "type": "alert", "content": "Should be rerouted to apex"}],
  "scratchpad_update": "",
  "status": "done"
}' > /tmp/test_response.txt
python3 "$APEX_HOME/services/parse_response.py" /tmp/test_response.txt
echo ""

# Test 4: Mixed status format
echo '--- Test 4: Status parsing ---'
for status in '"done"' '"blocked:no_search_tool"' '"needs_review:high"' '"done | blocked:none | needs_review:low"'; do
  echo "{\"actions_taken\":\"\",\"observations\":\"\",\"proposed_output\":\"\",\"messages\":[],\"scratchpad_update\":\"\",\"status\":$status}" > /tmp/test_response.txt
  RESULT=$(python3 "$APEX_HOME/services/parse_response.py" /tmp/test_response.txt | python3 -c "import json,sys; s=json.load(sys.stdin)['status']; print(f\"{s['state']}:{s.get('reason','')}{s.get('stakes','')}\")")
  echo "  Input: $status → Parsed: $RESULT"
done
echo ""

echo "=== Parser Tests Complete ==="
