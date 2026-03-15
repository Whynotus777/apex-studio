#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' .env | xargs)

echo "=== APEX Critic Evidence Tests ==="
echo ""

python3 - <<'PYEOF'
import math
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from kernel.critic_evidence import verify_agent_output
from kernel.evidence import EvidenceStore

schema_path = Path("db/schema.sql")
with tempfile.TemporaryDirectory() as tmpdir:
    db_path = Path(tmpdir) / "critic_evidence_test.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_path.read_text())
    conn.commit()
    conn.close()

    store = EvidenceStore(db_path)
    store.store_evidence(
        task_id="task-critic-evidence-001",
        agent_id="scout",
        tool_name="web_search",
        query="AI private equity deal analysis",
        results=[
            {"title": "Result 1", "url": "https://example.com/real-1", "snippet": "Snippet 1"},
            {"title": "Result 2", "url": "https://example.com/real-2", "snippet": "Snippet 2"},
            {"title": "Result 3", "url": "https://example.com/real-3", "snippet": "Snippet 3"},
        ],
    )

    agent_output = """
    Based on retrieved evidence:
    - https://example.com/real-1
    - https://example.com/real-2
    - https://example.com/invented
    """

    result = verify_agent_output("task-critic-evidence-001", agent_output, str(db_path))

    assert result["total_citations"] == 3, result
    assert result["verified"] == 2, result
    assert len(result["unverified"]) == 1, result
    assert result["unverified"][0] == "https://example.com/invented", result
    assert result["evidence_count"] == 1, result
    assert math.isclose(result["grounding_score"], 2 / 3, rel_tol=1e-9), result

    print("PASS verify_agent_output counts citations correctly")
    print("PASS verify_agent_output flags invented URLs")
    print("PASS grounding_score ~= 0.67")

print("")
print("=== Critic Evidence Tests Complete ===")
PYEOF
