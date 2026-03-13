#!/bin/bash
# Trigger Critic pipeline from Telegram or CLI
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

PENDING=$(sqlite3 "$APEX_HOME/db/apex_state.db" "SELECT COUNT(*) FROM reviews WHERE verdict IS NULL;")

if [ "$PENDING" = "0" ]; then
  echo "No pending reviews."
  exit 0
fi

echo "Processing $PENDING pending review(s)..."
python3 "$APEX_HOME/services/run_critic.py"
