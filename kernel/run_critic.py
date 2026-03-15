#!/usr/bin/env python3
"""
APEX Critic Pipeline — Processes the review queue.

1. Reads pending reviews from the DB
2. For each review, loads the agent's output (from session context)
3. Spawns Critic with the output + rubric
4. Parses Critic's verdict (PASS / REVISE / BLOCK)
5. Updates review record and task status
6. For HIGH stakes: flags for Abdul approval (does not auto-pass)

Usage: python3 kernel/run_critic.py [--dry-run]
"""
import os
import sys
import json
import sqlite3
import subprocess
import tempfile
from datetime import datetime

from kernel.critic_evidence import verify_agent_output

APEX_HOME = os.environ.get("APEX_HOME", os.path.expanduser("~/apex-studio"))
DB_PATH = os.path.join(APEX_HOME, "db", "apex_state.db")

def db_query(sql, params=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(sql, params or [])
    rows = [dict(r) for r in cur.fetchall()]
    conn.commit()
    conn.close()
    return rows

def db_execute(sql, params=None):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(sql, params or [])
    conn.commit()
    conn.close()

def get_pending_reviews():
    return db_query("""
        SELECT r.id, r.task_id, r.agent_name, r.output_ref, r.stakes,
               (SELECT s.context FROM agent_sessions s
                WHERE s.agent_name = r.agent_name AND s.task_id = r.task_id
                ORDER BY s.last_active DESC LIMIT 1) AS agent_output,
               t.title as task_title, t.description as task_desc,
               g.name as goal_name
        FROM reviews r
        LEFT JOIN tasks t ON r.task_id = t.id
        LEFT JOIN goals g ON t.goal_id = g.id
        WHERE r.verdict IS NULL
        ORDER BY
            CASE r.stakes WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
            r.created_at ASC
    """)

def build_critic_prompt(review):
    """Build system and user prompts for the Critic."""
    stakes = review["stakes"] or "low"
    agent_name = review["agent_name"]
    agent_output = review["agent_output"] or "(no output found)"
    task_title = review["task_title"] or "(no title)"
    task_desc = review["task_desc"] or "(no description)"
    goal_name = review["goal_name"] or "(no goal)"

    # Load critic hard rules
    rules_path = os.path.join(APEX_HOME, "templates", "startup-chief-of-staff", "agents", "critic", "constraints", "hard-rules.md")
    hard_rules = ""
    if os.path.exists(rules_path):
        with open(rules_path) as f:
            hard_rules = f.read()

    system_prompt = f"""You are Critic, the quality gate for APEX venture studio.
You are reviewing output from the {agent_name} agent.
Stakes level: {stakes.upper()}

Your rubric:
1. ACCURACY: Are claims sourced or clearly labeled as proposed/estimated? Any fabricated data?
2. COMPLETENESS: Does the output address the full task?
3. ACTIONABILITY: Can Abdul act on this without follow-up questions?
4. CONCISENESS: Is it as short as possible without losing substance?
5. HARD RULE COMPLIANCE: Does it violate any agent hard rules?
6. GROUNDING: Does the agent only claim actions it actually performed?

Hard rules:
{hard_rules}

Score each dimension 1-5. Then give an overall verdict.

Respond with ONLY a valid JSON object:
{{
  "scores": {{
    "accuracy": <1-5>,
    "completeness": <1-5>,
    "actionability": <1-5>,
    "conciseness": <1-5>,
    "hard_rule_compliance": <1-5>,
    "grounding": <1-5>
  }},
  "overall_score": <1.0-5.0>,
  "verdict": "PASS|REVISE|BLOCK",
  "feedback": "specific actionable feedback",
  "hard_rule_violations": ["list of violations or empty"],
  "grounding_issues": ["list of fabrications or empty"]
}}

Verdict rules:
- PASS: overall >= 3.5 and no hard rule violations
- REVISE: overall >= 2.5 or minor issues that can be fixed
- BLOCK: overall < 2.5 or hard rule violations found"""

    user_prompt = f"""Review this output:

Task: {task_title}
Description: {task_desc}
Goal: {goal_name}
Agent: {agent_name}
Stakes: {stakes}

--- AGENT OUTPUT ---
{agent_output[:2000]}
--- END OUTPUT ---

Score and provide your verdict."""

    return system_prompt, user_prompt


def call_critic(system_prompt, user_prompt):
    """Call the Critic model via call_model.py."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as sf:
        sf.write(system_prompt)
        sys_path = sf.name
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as uf:
        uf.write(user_prompt)
        usr_path = uf.name

    try:
        # Use Gemini Flash as primary; fall back to local only if no API key
        google_key = os.environ.get("GOOGLE_API_KEY", "")
        model = "gemini-3-flash-preview" if google_key else "qwen3.5-apex"
        result = subprocess.run(
            ["python3", os.path.join(APEX_HOME, "kernel", "call_model.py"),
             model, sys_path, usr_path, "0.1"],
            capture_output=True, text=True, timeout=120,
            stdin=subprocess.DEVNULL,
        )
        return result.stdout.strip()
    except subprocess.TimeoutExpired:
        return '{"error": "Critic model timed out"}'
    except Exception as e:
        return f'{{"error": "{str(e)}"}}'
    finally:
        os.unlink(sys_path)
        os.unlink(usr_path)


def parse_critic_response(response_text):
    """Parse Critic's JSON response."""
    # Try direct JSON
    try:
        return json.loads(response_text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    # Try extracting JSON from markdown code blocks
    import re
    patterns = [
        r'```json\s*(.*?)\s*```',
        r'```\s*(\{.*?\})\s*```',
        r'(\{[^{}]*"verdict"[^{}]*\})',
    ]
    for pattern in patterns:
        match = re.search(pattern, response_text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except (json.JSONDecodeError, ValueError):
                continue

    # Fallback: try to extract verdict from text
    verdict = "REVISE"
    if "PASS" in response_text.upper():
        verdict = "PASS"
    elif "BLOCK" in response_text.upper():
        verdict = "BLOCK"

    return {
        "scores": {},
        "overall_score": 0,
        "verdict": verdict,
        "feedback": response_text[:500],
        "hard_rule_violations": [],
        "grounding_issues": [],
        "_parse_method": "text_fallback"
    }


def process_review(review, dry_run=False):
    """Process a single review."""
    review_id = review["id"]
    task_id = review["task_id"]
    stakes = review["stakes"] or "low"
    agent_name = review["agent_name"]

    print(f"\n  Processing review #{review_id}")
    print(f"  Agent: {agent_name} | Task: {task_id} | Stakes: {stakes}")

    # Guard: skip reviews with empty session context rather than sending garbage to LLM
    agent_output = (review.get("agent_output") or "").strip()
    if not agent_output:
        print(f"  SKIPPED: no session context found for {agent_name} / {task_id}")
        db_execute("""
            UPDATE reviews SET
                verdict = 'SKIPPED',
                feedback = 'SKIPPED: empty session context — no agent output found for this task',
                reviewed_at = datetime('now')
            WHERE id = ?
        """, [review_id])
        return

    system_prompt, user_prompt = build_critic_prompt(review)

    if dry_run:
        print(f"  [DRY RUN] Would call Critic model with {len(system_prompt)} + {len(user_prompt)} chars")
        return

    # Call Critic
    print(f"  Calling Critic model...")
    raw_response = call_critic(system_prompt, user_prompt)
    print(f"  Response: {len(raw_response)} chars")

    # Parse
    parsed = parse_critic_response(raw_response)
    verdict = parsed.get("verdict", "REVISE")
    overall_score = parsed.get("overall_score", 0)
    feedback = parsed.get("feedback", "")
    scores = parsed.get("scores", {})
    violations = parsed.get("hard_rule_violations", [])
    grounding = parsed.get("grounding_issues", [])

    print(f"  Verdict: {verdict} | Score: {overall_score}/5")
    if violations:
        print(f"  Violations: {violations}")
    if grounding:
        print(f"  Grounding issues: {grounding}")
    print(f"  Feedback: {feedback[:200]}")

    evidence_verification = verify_agent_output(task_id, review.get("agent_output", "") or "", DB_PATH)
    grounding_score = float(evidence_verification.get("grounding_score", 1.0))
    parsed["evidence_verification"] = evidence_verification

    print(
        "  Evidence grounding:"
        f" {evidence_verification.get('verified', 0)}/{evidence_verification.get('total_citations', 0)}"
        f" verified ({grounding_score:.2f})"
    )

    if verdict == "PASS" and grounding_score < 0.5:
        unverified_urls = evidence_verification.get("unverified", [])
        verdict = "REVISE"
        feedback = (
            f"Critic passed but evidence grounding score is {grounding_score:.2f} — "
            f"agent cited unverified sources: {', '.join(unverified_urls)}"
        )
        parsed["verdict"] = verdict
        parsed["feedback"] = feedback
        print("  Override: PASS -> REVISE due to evidence grounding threshold")

    # Update review record
    db_execute("""
        UPDATE reviews SET
            verdict = ?,
            feedback = ?,
            triage_model = 'gemini-3-flash-preview',
            review_model = 'gemini-3-flash-preview',
            reviewed_at = datetime('now')
        WHERE id = ?
    """, [verdict, json.dumps(parsed), review_id])

    # Update task status based on verdict
    if verdict == "PASS":
        if stakes == "high":
            # High stakes: pass Critic but still needs Abdul approval
            db_execute("UPDATE tasks SET review_status='critic_passed', status='review' WHERE id=?", [task_id])
            print(f"  → Critic PASSED (HIGH stakes — awaiting Abdul approval)")
        else:
            db_execute("UPDATE tasks SET review_status='approved', status='done', completed_at=datetime('now'), checked_out_by=NULL WHERE id=?", [task_id])
            print(f"  → Task approved and completed")
    elif verdict == "REVISE":
        db_execute("UPDATE tasks SET review_status='needs_revision', status='backlog', checked_out_by=NULL WHERE id=?", [task_id])
        # Send feedback to the original agent
        db_execute("""
            INSERT INTO agent_messages (from_agent, to_agent, msg_type, content, task_id, priority)
            VALUES ('critic', ?, 'review_feedback', ?, ?, 1)
        """, [agent_name, f"REVISE: {feedback[:500]}", task_id])
        print(f"  → Sent back to {agent_name} for revision")
    elif verdict == "BLOCK":
        db_execute("UPDATE tasks SET review_status='blocked', status='blocked' WHERE id=?", [task_id])
        db_execute("""
            INSERT INTO agent_messages (from_agent, to_agent, msg_type, content, task_id, priority)
            VALUES ('critic', 'apex', 'escalation', ?, ?, 1)
        """, [f"BLOCKED by Critic: {feedback[:500]}\nViolations: {violations}", task_id])
        print(f"  → BLOCKED — escalated to Apex")

    # Log eval
    for dimension, score in scores.items():
        if isinstance(score, (int, float)):
            db_execute("""
            INSERT INTO evals (task_id, agent_name, eval_layer, eval_type, dimension, score, max_score, notes)
            VALUES (?, ?, 'critic', 'rubric', ?, ?, 5.0, ?)
        """, [task_id, agent_name, dimension, score, verdict])

    db_execute("""
        INSERT INTO evals (task_id, agent_name, eval_layer, eval_type, dimension, score, max_score, notes)
        VALUES (?, ?, 'critic', 'automated', 'evidence_grounding', ?, 1.0, ?)
    """, [task_id, agent_name, grounding_score, verdict])


def main():
    dry_run = "--dry-run" in sys.argv

    print("=== APEX Critic Pipeline ===")
    print(f"  Mode: {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    reviews = get_pending_reviews()
    print(f"  Pending reviews: {len(reviews)}")

    if not reviews:
        print("  No reviews to process.")
        return

    for review in reviews:
        process_review(review, dry_run)

    print("\n=== Critic Pipeline Complete ===")

    # Summary
    results = db_query("""
        SELECT verdict, COUNT(*) as cnt FROM reviews
        WHERE reviewed_at IS NOT NULL
        GROUP BY verdict
    """)
    print("\nVerdict Summary:")
    for r in results:
        print(f"  {r['verdict']}: {r['cnt']}")


if __name__ == "__main__":
    main()
