#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

echo "=== APEX Memory Tests ==="
echo ""

python3 - <<'PYEOF'
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, ".")

from kernel.memory import DurableMemory, SessionMemory, WorkingMemory

apex_home = Path.cwd()
db_path = apex_home / "db" / "apex_state.db"
scratchpad_path = apex_home / "templates" / "startup-chief-of-staff" / "agents" / "builder" / "workspace" / "scratchpad.md"
durable_path = apex_home / "templates" / "startup-chief-of-staff" / "workspace" / "MEMORY.md"

session_memory = SessionMemory(apex_home)
working_memory = WorkingMemory(apex_home)
durable_memory = DurableMemory(apex_home)

scratchpad_backup = scratchpad_path.read_text()
durable_backup = durable_path.read_text()
session_ids = ["memtest-session-001", "memtest-session-002"]

def cleanup() -> None:
    scratchpad_path.write_text(scratchpad_backup)
    durable_path.write_text(durable_backup)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "DELETE FROM agent_sessions WHERE id IN (?, ?)",
        session_ids,
    )
    conn.commit()
    conn.close()

cleanup()

try:
    print("--- SessionMemory ---")
    session_memory.save("builder", session_ids[0], "memtest-task-001", "Context one")
    session_memory.save("builder", session_ids[1], "memtest-task-002", "Context two")

    latest = session_memory.get_latest("builder")
    assert latest["id"] == session_ids[1], latest
    assert latest["task_id"] == "memtest-task-002", latest
    assert latest["context"] == "Context two", latest
    print("  PASS  get_latest returns newest saved session")

    history = session_memory.get_history("builder", limit=2)
    assert len(history) == 2, history
    assert history[0]["id"] == session_ids[1], history
    assert history[1]["id"] == session_ids[0], history
    print("  PASS  get_history returns ordered session list")

    print("\n--- WorkingMemory ---")
    before = working_memory.read("builder")
    working_memory.append("builder", "Memory test entry", session_ids[1])
    after = working_memory.read("builder")
    assert "Memory test entry" in after, after
    assert session_ids[1] in after, after
    assert len(after) > len(before), (len(before), len(after))
    print("  PASS  append writes session-tagged scratchpad entry")

    recent = working_memory.get_recent("builder", lines=3)
    assert "Memory test entry" in recent, recent
    print("  PASS  get_recent returns tail of scratchpad")

    working_memory.clear("builder")
    assert working_memory.read("builder") == "", working_memory.read("builder")
    print("  PASS  clear truncates scratchpad")

    print("\n--- DurableMemory ---")
    durable_memory.append("Meridian note: durable memory test")
    durable_contents = durable_memory.read()
    assert "Meridian note: durable memory test" in durable_contents, durable_contents
    print("  PASS  append persists to MEMORY.md")

    search_results = durable_memory.search("Meridian")
    assert any("durable memory test" in line for line in search_results), search_results
    print("  PASS  search returns matching lines")

    print("\n=== Memory Tests Complete ===")
finally:
    cleanup()
PYEOF
