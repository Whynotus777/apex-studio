#!/bin/bash
# heartbeat.sh — Triggered by cron, spawns agents based on schedule
# Usage: ./kernel/heartbeat.sh <agent_name>
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
AGENT="$1"

if [ -z "$AGENT" ]; then
  echo "Usage: heartbeat.sh <agent_name>"
  exit 1
fi

# Source env if it exists
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

# Log heartbeat
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Heartbeat: $AGENT" >> "$APEX_HOME/kernel/heartbeat.log"

# Spawn the agent
"$APEX_HOME/kernel/spawn-agent.sh" "$AGENT" >> "$APEX_HOME/kernel/heartbeat.log" 2>&1
