from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from kernel.api import ApexKernel

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "db" / "apex_state.db"
SCHEMA_PATH = ROOT / "db" / "schema.sql"
SEED_PATH = ROOT / "db" / "seed.sql"
SEED_TOOLS_PATH = ROOT / "db" / "seed_tools.sql"

DEMO_TEAMS: list[dict[str, str]] = [
    {"template_id": "content-engine", "name": "Demo Content Engine"},
    {"template_id": "gtm-engine", "name": "Demo GTM Engine"},
    {"template_id": "competitive-intel", "name": "Demo Competitive Intel"},
]


def ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA_PATH.read_text())
        conn.commit()
    finally:
        conn.close()


def run_sql_file(path: Path) -> None:
    if not path.exists():
        return
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(path.read_text())
        conn.commit()
    finally:
        conn.close()


def get_or_launch_workspace(kernel: ApexKernel, template_id: str, name: str) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT id FROM workspaces WHERE template_id = ? AND name = ? LIMIT 1",
            (template_id, name),
        ).fetchone()
    if row:
        return str(row[0])
    launched = kernel.launch_template(template_id, overrides={"workspace_name": name})
    return str(launched["workspace_id"])


def upsert_task(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    goal_id: str,
    title: str,
    description: str,
    pipeline_stage: str,
    status: str,
    review_status: str | None,
    assigned_to: str,
    workspace_id: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO tasks (
            id, goal_id, title, description, pipeline_stage, assigned_to,
            status, priority, review_status, workspace_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 2, ?, ?)
        """,
        (
            task_id,
            goal_id,
            title,
            description,
            pipeline_stage,
            assigned_to,
            status,
            review_status,
            workspace_id,
        ),
    )
    conn.execute(
        """
        UPDATE tasks
        SET goal_id = ?, title = ?, description = ?, pipeline_stage = ?, assigned_to = ?,
            status = ?, review_status = ?, workspace_id = ?
        WHERE id = ?
        """,
        (
            goal_id,
            title,
            description,
            pipeline_stage,
            assigned_to,
            status,
            review_status,
            workspace_id,
            task_id,
        ),
    )


def upsert_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    agent_name: str,
    task_id: str,
    payload: dict[str, Any],
    status: str = "complete",
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO agent_sessions (id, agent_name, task_id, context, last_active, status)
        VALUES (?, ?, ?, ?, datetime('now'), ?)
        """,
        (session_id, agent_name, task_id, json.dumps(payload), status),
    )


def ensure_demo_content(conn: sqlite3.Connection, workspace_id: str) -> None:
    writer = f"{workspace_id}-writer"
    scout = f"{workspace_id}-scout"
    critic = f"{workspace_id}-critic"
    task_id = "demo-content-linkedin"
    session_id = "demo-session-content-writer"

    upsert_task(
        conn,
        task_id=task_id,
        goal_id="goal-meridian",
        title="Draft a LinkedIn carousel on AI agents in private equity",
        description="Create a short operator-grade content draft backed by recent sources.",
        pipeline_stage="create",
        status="review",
        review_status="critic_passed",
        assigned_to=writer,
        workspace_id=workspace_id,
    )

    upsert_session(
        conn,
        session_id=session_id,
        agent_name=writer,
        task_id=task_id,
        payload={
            "actions_taken": "Drafted a LinkedIn carousel with three slides and a CTA.",
            "observations": "Operator prefers concise, high-signal B2B tone.",
            "proposed_output": (
                "Slide 1: Private equity teams are drowning in diligence noise.\\n\\n"
                "Slide 2: AI agents can turn CIMs, data rooms, and market signals into structured conviction faster.\\n\\n"
                "Slide 3: The winning firms will not replace analysts. They will compound analysts with better systems.\\n\\n"
                "CTA: If you are building in PE workflows, compare your current diligence loop against an evidence-backed agent workflow."
            ),
            "messages": [{"to": critic, "type": "review_request", "content": "Ready for review."}],
            "scratchpad_update": "LinkedIn draft angle: analysts compounded by systems, not replaced.",
            "status": "needs_review:medium",
        },
    )

    feedback = {
        "summary": "Strong operator-facing draft with verified claims.",
        "scores": {
            "accuracy": 4.0,
            "completeness": 4.0,
            "actionability": 4.0,
            "conciseness": 4.0,
            "hard_rule_compliance": 5.0,
            "grounding": 4.2,
        },
    }
    conn.execute(
        """
        INSERT OR IGNORE INTO reviews (id, task_id, agent_name, output_ref, stakes, verdict, feedback, reviewed_at, workspace_id)
        VALUES (1001, ?, ?, ?, 'medium', 'PASS', ?, datetime('now'), ?)
        """,
        (task_id, writer, session_id, json.dumps(feedback), workspace_id),
    )
    conn.execute(
        """
        UPDATE reviews
        SET agent_name = ?, output_ref = ?, stakes = 'medium', verdict = 'PASS', feedback = ?, workspace_id = ?, reviewed_at = datetime('now')
        WHERE id = 1001
        """,
        (writer, session_id, json.dumps(feedback), workspace_id),
    )

    evidence_results = [
        {
            "title": "Bain & Company — Will AI Disrupt SaaS?",
            "url": "https://www.bain.com/insights/will-ai-disrupt-saas/",
            "snippet": "Bain explores how AI-native workflows may change the structure of software buying and operating models.",
        },
        {
            "title": "McKinsey — The state of AI in early 2026",
            "url": "https://www.mckinsey.com/capabilities/quantumblack/our-insights/the-state-of-ai-in-2026",
            "snippet": "A survey of AI adoption, deployment patterns, and where enterprises are capturing value.",
        },
        {
            "title": "TechStartups — AI agents vs SaaS",
            "url": "https://techstartups.com/2026/03/01/ai-agents-vs-saas/",
            "snippet": "An operator-oriented summary of how AI agent products are changing B2B workflow expectations.",
        },
    ]
    conn.execute(
        """
        INSERT OR REPLACE INTO evidence (id, task_id, agent_id, tool_name, query, results, created_at)
        VALUES ('demo-evidence-content', ?, ?, 'web_search', 'AI private equity workflow automation March 2026', ?, datetime('now'))
        """,
        (task_id, scout, json.dumps(evidence_results)),
    )

    conn.execute(
        """
        INSERT OR IGNORE INTO agent_messages (from_agent, to_agent, msg_type, content, task_id, workspace_id)
        VALUES (?, ?, 'review_request', 'Draft ready for approval queue.', ?, ?)
        """,
        (writer, critic, task_id, workspace_id),
    )


def ensure_demo_gtm(conn: sqlite3.Connection, workspace_id: str) -> None:
    strategist = f"{workspace_id}-strategist"
    task_id = "demo-gtm-positioning"
    session_id = "demo-session-gtm-strategist"
    upsert_task(
        conn,
        task_id=task_id,
        goal_id="goal-meridian",
        title="Define GTM messaging for evidence-backed diligence agents",
        description="Position the product against generic copilots and manual analyst workflows.",
        pipeline_stage="strategize",
        status="done",
        review_status="approved",
        assigned_to=strategist,
        workspace_id=workspace_id,
    )
    upsert_session(
        conn,
        session_id=session_id,
        agent_name=strategist,
        task_id=task_id,
        payload={
            "actions_taken": "Built a 3-part positioning memo for founder-led PE tools.",
            "observations": "Differentiation is strongest on trust and workflow speed.",
            "proposed_output": "Core message: evidence-backed diligence automation for lean investment teams.",
            "messages": [],
            "scratchpad_update": "Position against generic copilots: trust loop + approvals + evidence.",
            "status": "done",
        },
    )


def ensure_demo_competitive(conn: sqlite3.Connection, workspace_id: str) -> None:
    analyst = f"{workspace_id}-analyst"
    task_id = "demo-competitive-weekly"
    session_id = "demo-session-competitive-analyst"
    upsert_task(
        conn,
        task_id=task_id,
        goal_id="goal-meridian",
        title="Weekly competitor change briefing",
        description="Summarize pricing, launch, and hiring changes from the last 7 days.",
        pipeline_stage="analyze",
        status="in_progress",
        review_status=None,
        assigned_to=analyst,
        workspace_id=workspace_id,
    )
    upsert_session(
        conn,
        session_id=session_id,
        agent_name=analyst,
        task_id=task_id,
        payload={
            "actions_taken": "Compared the latest competitor findings against prior observations.",
            "observations": "Two competitors shifted messaging toward AI automation; one added a senior PE data role.",
            "proposed_output": "Weekly briefing draft in progress.",
            "messages": [],
            "scratchpad_update": "Watch pricing and hiring pages daily.",
            "status": "in_progress",
        },
        status="active",
    )


def main() -> None:
    ensure_db()
    run_sql_file(SEED_PATH)
    run_sql_file(SEED_TOOLS_PATH)

    kernel = ApexKernel(ROOT)

    workspace_ids: dict[str, str] = {}
    for team in DEMO_TEAMS:
        workspace_ids[team["template_id"]] = get_or_launch_workspace(kernel, team["template_id"], team["name"])

    conn = sqlite3.connect(DB_PATH)
    try:
        ensure_demo_content(conn, workspace_ids["content-engine"])
        ensure_demo_gtm(conn, workspace_ids["gtm-engine"])
        ensure_demo_competitive(conn, workspace_ids["competitive-intel"])
        conn.commit()
    finally:
        conn.close()

    print(json.dumps({"seeded": True, "workspaces": workspace_ids}, indent=2))


if __name__ == "__main__":
    main()
