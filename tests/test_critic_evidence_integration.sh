#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' .env | xargs)

echo "=== APEX Critic Evidence Integration Test ==="
echo ""

python3 - <<'PYEOF'
import importlib
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

repo_root = Path.cwd()
sys.path.insert(0, str(repo_root))

schema_text = (repo_root / "db" / "schema.sql").read_text()
critic_rules = (
    repo_root
    / "templates"
    / "startup-chief-of-staff"
    / "agents"
    / "critic"
    / "constraints"
    / "hard-rules.md"
).read_text()

with tempfile.TemporaryDirectory() as tmpdir:
    tmp_root = Path(tmpdir)
    (tmp_root / "db").mkdir(parents=True, exist_ok=True)
    (tmp_root / "templates" / "startup-chief-of-staff" / "agents" / "critic" / "constraints").mkdir(
        parents=True, exist_ok=True
    )
    (tmp_root / "templates" / "startup-chief-of-staff" / "agents" / "critic" / "constraints" / "hard-rules.md").write_text(
        critic_rules
    )

    db_path = tmp_root / "db" / "apex_state.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(schema_text)

    conn.execute("INSERT INTO goals (id, name) VALUES ('goal-test', 'Goal Test')")
    conn.execute(
        """
        INSERT INTO tasks (id, goal_id, title, description, pipeline_stage, assigned_to, status, review_status)
        VALUES ('critic-evidence-int-001', 'goal-test', 'Critic Evidence Integration', 'Verify evidence grounding override', 'analyze', 'analyst', 'review', 'pending')
        """
    )
    conn.execute(
        """
        INSERT INTO agent_sessions (id, agent_name, task_id, context, status)
        VALUES (?, ?, ?, ?, 'active')
        """,
        (
            "sess-critic-evidence-test",
            "analyst",
            "critic-evidence-int-001",
            (
                "Here are my sources:\n"
                "https://example.com/real-1\n"
                "https://example.com/fake-1\n"
                "https://example.com/fake-1\n"
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO reviews (task_id, agent_name, output_ref, stakes)
        VALUES ('critic-evidence-int-001', 'analyst', 'sess-critic-evidence-test', 'low')
        """
    )
    conn.execute(
        """
        INSERT INTO evidence (id, task_id, agent_id, tool_name, query, results)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "ev-critic-evidence-001",
            "critic-evidence-int-001",
            "scout",
            "web_search",
            "integration query",
            json.dumps(
                [
                    {"title": "Real 1", "url": "https://example.com/real-1", "snippet": "Verified source"},
                    {"title": "Real 2", "url": "https://example.com/real-2", "snippet": "Verified source"},
                    {"title": "Real 3", "url": "https://example.com/real-3", "snippet": "Verified source"},
                ]
            ),
        ),
    )
    conn.commit()
    conn.close()

    os.environ["APEX_HOME"] = str(tmp_root)
    rc = importlib.import_module("kernel.run_critic")
    rc.APEX_HOME = str(tmp_root)
    rc.DB_PATH = str(db_path)
    rc.call_critic = lambda system_prompt, user_prompt: json.dumps(
        {
            "scores": {
                "accuracy": 4,
                "completeness": 4,
                "actionability": 4,
                "conciseness": 4,
                "hard_rule_compliance": 4,
                "grounding": 4,
            },
            "overall_score": 4.0,
            "verdict": "PASS",
            "feedback": "Looks good",
            "hard_rule_violations": [],
            "grounding_issues": [],
        }
    )

    reviews = rc.get_pending_reviews()
    assert len(reviews) == 1, reviews
    rc.process_review(reviews[0], dry_run=False)

    conn = sqlite3.connect(db_path)
    review_row = conn.execute(
        "SELECT verdict, feedback FROM reviews WHERE task_id='critic-evidence-int-001'"
    ).fetchone()
    task_row = conn.execute(
        "SELECT status, review_status FROM tasks WHERE id='critic-evidence-int-001'"
    ).fetchone()
    eval_row = conn.execute(
        """
        SELECT dimension, score, eval_type
        FROM evals
        WHERE task_id='critic-evidence-int-001' AND dimension='evidence_grounding'
        """
    ).fetchone()
    conn.close()

    feedback_json = json.loads(review_row[1])
    evidence_verification = feedback_json["evidence_verification"]

    assert review_row[0] == "REVISE", review_row
    assert "evidence grounding score is 0.33" in feedback_json["feedback"], feedback_json
    assert evidence_verification["verified"] == 1, evidence_verification
    assert len(evidence_verification["unverified"]) == 2, evidence_verification
    assert task_row[0] == "backlog", task_row
    assert task_row[1] == "needs_revision", task_row
    assert eval_row[0] == "evidence_grounding", eval_row
    assert abs(eval_row[1] - (1 / 3)) < 1e-9, eval_row
    assert eval_row[2] == "automated", eval_row

    print("PASS evidence_grounding eval logged")
    print("PASS PASS->REVISE override triggered on low grounding score")
    print("PASS evidence verification added to review feedback JSON")

print("")
print("=== Critic Evidence Integration Test Complete ===")
PYEOF
