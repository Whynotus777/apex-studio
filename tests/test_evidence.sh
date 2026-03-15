#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' .env | xargs)

echo "=== APEX Evidence Tests ==="
echo ""

python3 - <<'PYEOF'
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from kernel.evidence import EvidenceStore

schema_path = Path("db/schema.sql")
with tempfile.TemporaryDirectory() as tmpdir:
    db_path = Path(tmpdir) / "evidence_test.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_path.read_text())
    conn.commit()
    conn.close()

    store = EvidenceStore(db_path)
    results = [
        {
            "title": "AI in Private Equity",
            "url": "https://example.com/ai-pe",
            "snippet": "How AI changes private equity workflows.",
        },
        {
            "title": "Deal Analysis Guide",
            "url": "https://example.com/deal-analysis",
            "snippet": "A guide to AI-powered deal analysis.",
        },
    ]

    evidence_id = store.store_evidence(
        task_id="task-evidence-001",
        agent_id="scout",
        tool_name="web_search",
        query="AI private equity deal analysis",
        results=results,
    )
    assert evidence_id.startswith("ev-"), evidence_id
    print("PASS store_evidence returns evidence id")

    task_evidence = store.get_evidence("task-evidence-001")
    assert len(task_evidence) == 1, task_evidence
    assert task_evidence[0]["results"][0]["title"] == "AI in Private Equity", task_evidence
    print("PASS get_evidence returns stored evidence")

    single = store.get_evidence_by_id(evidence_id)
    assert single["query"] == "AI private equity deal analysis", single
    assert single["results"][1]["url"] == "https://example.com/deal-analysis", single
    print("PASS get_evidence_by_id returns single record")

    assert store.verify_citation("task-evidence-001", "https://example.com/ai-pe") is True
    assert store.verify_citation("task-evidence-001", "https://example.com/missing") is False
    print("PASS verify_citation checks URLs against stored evidence")

    prompt = store.format_for_prompt("task-evidence-001")
    assert "## Search Evidence" in prompt, prompt
    assert "AI in Private Equity" in prompt, prompt
    assert "https://example.com/deal-analysis" in prompt, prompt
    print("PASS format_for_prompt renders evidence block")

print("")
print("=== Evidence Tests Complete ===")
PYEOF
