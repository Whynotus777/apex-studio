#!/usr/bin/env python3
"""
scripts/e2e_test.py — Automated end-to-end API contract verification for APEX.

Tests every endpoint in api-contract.ts against a running backend.
Asserts response status codes, key fields, and type shapes.
Runs the full approval flow: get pending → approve → verify removed.

Usage:
    PYTHONPATH=. python3 scripts/seed_demo.py        # seed first
    PYTHONPATH=. python3 scripts/e2e_test.py         # then run tests

Requires: requests library (pip install requests)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:
    print("ERROR: requests library not installed. Run: pip install requests")
    sys.exit(1)

BASE = "http://localhost:8000"
DEMO_MKT_WS  = "ws-demo-marketing"
DEMO_SALES_WS = "ws-demo-sales"
DEMO_INVESTOR_WS = "ws-demo-investors"
DEMO_TASK_ID = "task-demo-mkt-002"
DEMO_INVESTOR_TASK_ID = "task-demo-investors-002"
DEMO_INVESTOR_DONE_TASK_ID = "task-demo-investors-001"

PASS = 0
FAIL = 0
results: list[tuple[str, str, str]] = []   # (name, status, detail)


def ok(name: str, detail: str = "") -> None:
    global PASS
    PASS += 1
    results.append((name, "PASS", detail))
    print(f"  ✅  {name}")


def fail(name: str, detail: str) -> None:
    global FAIL
    FAIL += 1
    results.append((name, "FAIL", detail))
    print(f"  ❌  {name}: {detail}")


def assert_fields(obj: dict, required: list[str], test_name: str) -> bool:
    missing = [f for f in required if f not in obj]
    if missing:
        fail(test_name, f"Missing fields: {missing}")
        return False
    return True


def seed_demo() -> None:
    """Run seed_demo.py to ensure fresh, known state."""
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "seed_demo.py")],
        capture_output=True,
        text=True,
        cwd=str(root),
        env={**__import__("os").environ, "PYTHONPATH": str(root)},
    )
    if result.returncode != 0:
        print(f"WARN: seed_demo.py failed:\n{result.stderr}")
    else:
        print(f"  Seeded: {result.stdout.strip()}")


# ── Test helpers ──────────────────────────────────────────────────────

def get(path: str, **kwargs: Any) -> requests.Response:
    return requests.get(f"{BASE}{path}", timeout=10, **kwargs)


def post(path: str, body: dict | None = None, timeout: int = 10, **kwargs: Any) -> requests.Response:
    return requests.post(f"{BASE}{path}", json=body or {}, timeout=timeout, **kwargs)


# ── Tests ─────────────────────────────────────────────────────────────

def test_api_health() -> None:
    print("\n── Health ────────────────────────────────────────────────")
    try:
        r = get("/api/teams")
        if r.status_code == 200:
            ok("API reachable at localhost:8000")
        else:
            fail("API reachable", f"HTTP {r.status_code}")
    except requests.ConnectionError:
        fail("API reachable", "Connection refused — is the API running?")


def test_get_teams() -> None:
    print("\n── GET /api/teams ────────────────────────────────────────")
    r = get("/api/teams")
    if r.status_code != 200:
        fail("GET /api/teams status 200", f"got {r.status_code}")
        return
    ok("GET /api/teams returns 200")

    teams: list[dict] = r.json()
    if not isinstance(teams, list):
        fail("GET /api/teams returns array", f"got {type(teams)}")
        return
    ok("GET /api/teams returns array")

    # Find demo team
    demo = next((t for t in teams if t.get("id") == DEMO_MKT_WS), None)
    if not demo:
        fail("Demo marketing team present", f"ws-demo-marketing not in {[t.get('id') for t in teams[:5]]}")
        return
    ok("Demo marketing team present")

    required = ["id", "name", "template_id", "template_name", "status", "agent_count", "created_at", "pending_approvals"]
    if assert_fields(demo, required, "TeamSummary shape"):
        ok("TeamSummary has required fields")

    if demo["status"] in ("active", "paused", "archived", "deleted"):
        ok("TeamSummary.status is valid enum")
    else:
        fail("TeamSummary.status valid", f"got '{demo['status']}'")

    if isinstance(demo["pending_approvals"], int):
        ok("TeamSummary.pending_approvals is int")
    else:
        fail("TeamSummary.pending_approvals int", f"got {type(demo['pending_approvals'])}")

    investor = next((t for t in teams if t.get("id") == DEMO_INVESTOR_WS), None)
    if investor:
        ok("Demo investor team present")
        if investor.get("template_id") == "investor-research":
            ok("Investor team template_id is investor-research")
        else:
            fail("Investor team template_id", f"got {investor.get('template_id')!r}")
    else:
        fail("Demo investor team present", f"{DEMO_INVESTOR_WS} not in {[t.get('id') for t in teams[:10]]}")


def test_get_team_detail() -> None:
    print("\n── GET /api/teams/{id} ───────────────────────────────────")
    r = get(f"/api/teams/{DEMO_MKT_WS}")
    if r.status_code != 200:
        fail(f"GET /api/teams/{DEMO_MKT_WS} 200", f"got {r.status_code}")
        return
    ok(f"GET /api/teams/{DEMO_MKT_WS} returns 200")

    team: dict = r.json()
    if assert_fields(team, ["id", "name", "members", "pending_approvals"], "TeamDetail shape"):
        ok("TeamDetail has required fields including members")

    members = team.get("members", [])
    if isinstance(members, list) and len(members) > 0:
        ok(f"TeamDetail.members non-empty ({len(members)} members)")
        m = members[0]
        required = ["agent_name", "role", "status", "last_heartbeat", "current_task"]
        if assert_fields(m, required, "AgentMember shape"):
            ok("AgentMember has required fields")
    else:
        fail("TeamDetail.members non-empty", f"got {members!r}")

    r404 = get("/api/teams/ws-does-not-exist")
    if r404.status_code == 404:
        ok("GET /api/teams/{bad-id} returns 404")
    else:
        fail("GET /api/teams/{bad-id} 404", f"got {r404.status_code}")


def test_get_team_members() -> None:
    print("\n── GET /api/teams/{id}/members ───────────────────────────")
    r = get(f"/api/teams/{DEMO_MKT_WS}/members")
    if r.status_code != 200:
        fail("GET /members 200", f"got {r.status_code}")
        return
    ok("GET /members returns 200")
    members: list = r.json()
    if isinstance(members, list) and len(members) == 4:
        ok(f"Members list has 4 items")
    else:
        fail("Members list has 4 items", f"got {len(members) if isinstance(members, list) else members!r}")


def test_get_investor_team_members() -> None:
    print("\n── GET /api/teams/{id}/members (investor) ────────────────")
    r = get(f"/api/teams/{DEMO_INVESTOR_WS}/members")
    if r.status_code != 200:
        fail("GET /members for investor team 200", f"got {r.status_code}")
        return
    ok("GET /members for investor team returns 200")

    members: list = r.json()
    if isinstance(members, list) and len(members) == 4:
        ok("Investor team has 4 members")
        roles = {m.get("role") for m in members}
        if roles == {"discovery", "enrichment", "outreach", "quality_gate"}:
            ok("Investor team role mix matches investor-research template")
        else:
            fail("Investor team role mix", f"got {sorted(roles)!r}")
    else:
        fail("Investor team has 4 members", f"got {len(members) if isinstance(members, list) else members!r}")


def test_get_team_tasks() -> None:
    print("\n── GET /api/teams/{id}/tasks ─────────────────────────────")
    r = get(f"/api/teams/{DEMO_MKT_WS}/tasks")
    if r.status_code != 200:
        fail("GET /tasks 200", f"got {r.status_code}")
        return
    ok("GET /tasks returns 200")

    tasks: list = r.json()
    if not isinstance(tasks, list):
        fail("GET /tasks returns list", f"got {type(tasks)}")
        return
    ok(f"GET /tasks returns list ({len(tasks)} tasks)")

    # Find the pending-review task
    pending = [t for t in tasks if t.get("review_status") == "critic_passed"]
    if pending:
        ok("At least 1 task with review_status=critic_passed")
        t = pending[0]
        required = ["id", "title", "status", "review_status", "created_at", "events"]
        if assert_fields(t, required, "TeamTask shape"):
            ok("TeamTask has required fields")
        if isinstance(t.get("events"), list):
            ok("TeamTask.events is a list")
        else:
            fail("TeamTask.events is list", f"got {type(t.get('events'))}")
    else:
        fail("Task with critic_passed exists", f"statuses: {[t.get('review_status') for t in tasks]}")


def test_get_investor_team_tasks() -> None:
    print("\n── GET /api/teams/{id}/tasks (investor) ──────────────────")
    r = get(f"/api/teams/{DEMO_INVESTOR_WS}/tasks")
    if r.status_code != 200:
        fail("GET /tasks for investor team 200", f"got {r.status_code}")
        return
    ok("GET /tasks for investor team returns 200")

    tasks: list = r.json()
    if not isinstance(tasks, list):
        fail("GET /tasks for investor team returns list", f"got {type(tasks)}")
        return
    ok(f"Investor team task list returns list ({len(tasks)} tasks)")

    investor_task = next((t for t in tasks if t.get("id") == DEMO_INVESTOR_TASK_ID), None)
    if not investor_task:
        fail("Investor pending approval task present", f"{DEMO_INVESTOR_TASK_ID!r} not found")
        return
    ok("Investor pending approval task present")

    if investor_task.get("status") == "review" and investor_task.get("review_status") == "critic_passed":
        ok("Investor pending task is in review with critic_passed")
    else:
        fail(
            "Investor pending task status",
            f"got status={investor_task.get('status')!r} review_status={investor_task.get('review_status')!r}",
        )

    events = investor_task.get("events")
    if isinstance(events, list) and len(events) > 0:
        ok("Investor pending task includes chain events")
    else:
        fail("Investor pending task events", f"got {events!r}")


def test_get_approvals() -> None:
    print("\n── GET /api/approvals ────────────────────────────────────")
    r = get("/api/approvals")
    if r.status_code != 200:
        fail("GET /api/approvals 200", f"got {r.status_code}")
        return
    ok("GET /api/approvals returns 200")

    approvals: list = r.json()
    if not isinstance(approvals, list):
        fail("GET /api/approvals returns list", f"got {type(approvals)}")
        return
    ok(f"GET /api/approvals returns list ({len(approvals)} items)")

    if len(approvals) >= 2:
        ok("At least 2 pending approvals after seed")
        a = approvals[0]
        required = ["id", "task_id", "task_title", "agent_name", "stakes", "team_id", "team_name", "verdict"]
        if assert_fields(a, required, "ApprovalItem shape"):
            ok("ApprovalItem has required fields")
        if isinstance(a["id"], int):
            ok("ApprovalItem.id is int (usable as approval endpoint param)")
        else:
            fail("ApprovalItem.id is int", f"got {type(a['id'])}: {a['id']!r}")
    else:
        fail("At least 2 pending approvals", f"got {len(approvals)}")

    # Test team filter
    r2 = get(f"/api/approvals?team_id={DEMO_MKT_WS}")
    if r2.status_code == 200:
        filtered: list = r2.json()
        wrong_team = [a for a in filtered if a.get("team_id") != DEMO_MKT_WS]
        if not wrong_team:
            ok("GET /api/approvals?team_id= filter works")
        else:
            fail("Approval team_id filter", f"returned {len(wrong_team)} wrong-team items")
    else:
        fail("GET /api/approvals?team_id= 200", f"got {r2.status_code}")


def test_task_output() -> None:
    print("\n── GET /api/tasks/{id}/output ────────────────────────────")
    r = get(f"/api/tasks/{DEMO_TASK_ID}/output")
    if r.status_code != 200:
        fail("GET /output 200", f"got {r.status_code}")
        return
    ok("GET /output returns 200")

    out: dict = r.json()
    if assert_fields(out, ["task_id", "task_title", "content"], "TaskOutput shape"):
        ok("TaskOutput has required fields")
    if isinstance(out.get("content"), str) and len(out["content"]) > 10:
        ok(f"TaskOutput.content is non-empty string ({len(out['content'])} chars)")
    else:
        fail("TaskOutput.content non-empty", f"got {out.get('content')!r}")

    r404 = get("/api/tasks/task-does-not-exist/output")
    if r404.status_code == 404:
        ok("GET /output for unknown task returns 404")
    else:
        fail("GET /output 404 for unknown task", f"got {r404.status_code}")


def test_task_evidence() -> None:
    print("\n── GET /api/tasks/{id}/evidence ──────────────────────────")
    r = get(f"/api/tasks/{DEMO_TASK_ID}/evidence")
    if r.status_code != 200:
        fail("GET /evidence 200", f"got {r.status_code}")
        return
    ok("GET /evidence returns 200")

    sources: list = r.json()
    if isinstance(sources, list):
        ok(f"GET /evidence returns list ({len(sources)} sources)")
    else:
        fail("GET /evidence returns list", f"got {type(sources)}")
        return

    if len(sources) > 0:
        s = sources[0]
        if assert_fields(s, ["url", "title"], "Source shape"):
            ok("Source has url and title")
        if s["url"].startswith("http"):
            ok("Source.url is valid URL")
        else:
            fail("Source.url starts with http", f"got {s['url']!r}")
    else:
        fail("Evidence list non-empty", "got empty list for seeded task")


def test_task_reviews() -> None:
    print("\n── GET /api/tasks/{id}/reviews ───────────────────────────")
    r = get(f"/api/tasks/{DEMO_TASK_ID}/reviews")
    if r.status_code != 200:
        fail("GET /reviews 200", f"got {r.status_code}")
        return
    ok("GET /reviews returns 200")

    rev = r.json()
    if rev is None:
        fail("GET /reviews non-null for seeded task", "got null")
        return
    ok("GET /reviews returns review object (not null)")

    required = ["id", "task_id", "verdict", "overall_score", "feedback", "dimensions"]
    if assert_fields(rev, required, "CriticReview shape"):
        ok("CriticReview has required fields")

    verdict = rev.get("verdict")
    # Verdict can be PASS/REVISE/BLOCK from the Critic, or 'approved'/'rejected'/
    # 'needs_revision' after human action. All are valid string values.
    if isinstance(verdict, str) and len(verdict) > 0:
        ok(f"CriticReview.verdict is non-empty string ({verdict!r})")
    else:
        fail("CriticReview.verdict non-empty string", f"got {verdict!r}")

    # overall_score and dimensions may be null if the review was modified by a
    # human action (reject/revise clears the score). Accept null as valid.
    score = rev.get("overall_score")
    if score is None or isinstance(score, (int, float)):
        ok(f"CriticReview.overall_score is number or null ({score!r})")
    else:
        fail("CriticReview.overall_score number or null", f"got {type(score)}: {score!r}")

    dims = rev.get("dimensions")
    if dims is None or (isinstance(dims, list) and len(dims) > 0):
        dims_desc = "null" if dims is None else str(len(dims))
        ok(f"CriticReview.dimensions is list or null ({dims_desc})")
        if isinstance(dims, list) and dims:
            d = dims[0]
            if assert_fields(d, ["name", "score"], "ReviewDimension shape"):
                ok("ReviewDimension has name and score")
    else:
        fail("CriticReview.dimensions list or null", f"got {dims!r}")

    # review.id should be usable as approval endpoint param
    if isinstance(rev.get("id"), int):
        ok(f"CriticReview.id is int ({rev['id']}) — usable as approval ID")
    else:
        fail("CriticReview.id is int", f"got {type(rev.get('id'))}: {rev.get('id')!r}")


def test_task_chain() -> None:
    print("\n── GET /api/tasks/{id}/chain ─────────────────────────────")
    r = get(f"/api/tasks/{DEMO_TASK_ID}/chain")
    if r.status_code != 200:
        fail("GET /chain 200", f"got {r.status_code}")
        return
    ok("GET /chain returns 200")

    data: dict = r.json()
    if assert_fields(data, ["task", "progress", "session_count", "review_count", "message_count"], "TaskChainResponse shape"):
        ok("TaskChainResponse has required fields")

    progress = data.get("progress", [])
    if isinstance(progress, list):
        ok(f"TaskChainResponse.progress is list ({len(progress)} items)")
    else:
        fail("TaskChainResponse.progress is list", f"got {type(progress)}")
        return

    if len(progress) > 0:
        step = progress[0]
        if "agent" in step and "type" in step:
            ok("ChainProgressItem has agent and type fields")
        else:
            fail("ChainProgressItem shape", f"got keys: {list(step.keys())}")
        # Backend uses `created_at`, not `completed_at` — verify the field name
        if "created_at" in step:
            ok("ChainProgressItem uses `created_at` field (api.ts maps → completed_at)")
        else:
            fail("ChainProgressItem has created_at", f"got keys: {list(step.keys())}")


def test_investor_task_chain() -> None:
    print("\n── GET /api/tasks/{id}/chain (investor) ──────────────────")
    r = get(f"/api/tasks/{DEMO_INVESTOR_TASK_ID}/chain")
    if r.status_code != 200:
        fail("GET /chain for investor task 200", f"got {r.status_code}")
        return
    ok("GET /chain for investor task returns 200")

    data: dict = r.json()
    progress = data.get("progress", [])
    if isinstance(progress, list) and progress:
        ok("Investor task chain has progress entries")
        agents = {step.get("agent") for step in progress}
        if any(agent and "investors-scout" in agent for agent in agents) and any(
            agent and "investors-analyst" in agent for agent in agents
        ) and any(agent and "investors-strategist" in agent for agent in agents):
            ok("Investor task chain includes scout, analyst, and strategist activity")
        else:
            fail("Investor task chain agent coverage", f"got {sorted(agents)!r}")
    else:
        fail("Investor task chain progress", f"got {progress!r}")


def test_get_templates_list() -> None:
    print("\n── GET /api/templates ────────────────────────────────────")
    r = get("/api/templates")
    if r.status_code != 200:
        fail("GET /api/templates 200", f"got {r.status_code}")
        return
    ok("GET /api/templates returns 200")
    templates: list[dict] = r.json()
    ids = {t.get("id") for t in templates}
    if "content-engine" in ids and "investor-research" in ids:
        ok("Templates list includes content-engine and investor-research")
    else:
        fail("Templates list includes required templates", f"got {sorted(ids)!r}")


def test_get_template_detail() -> None:
    print("\n── GET /api/templates/{id} ───────────────────────────────")
    r = get("/api/templates/content-engine")
    if r.status_code != 200:
        fail("GET /api/templates/content-engine 200", f"got {r.status_code}")
        return
    ok("GET /api/templates/content-engine returns 200")
    payload: dict = r.json()
    ui_schema = payload.get("ui_schema") or {}
    context_label = ((ui_schema.get("review_page") or {}).get("context_label"))
    if context_label == "Sources":
        ok("content-engine template detail has context_label == Sources")
    else:
        fail("content-engine context_label", f"got {context_label!r}")


def test_get_team_ui_schema() -> None:
    print("\n── GET /api/teams/{id}/ui-schema ─────────────────────────")
    r = get(f"/api/teams/{DEMO_MKT_WS}/ui-schema")
    if r.status_code != 200:
        fail("GET team ui-schema 200", f"got {r.status_code}")
        return
    ok("GET team ui-schema returns 200")
    schema: dict = r.json()
    context_label = ((schema.get("review_page") or {}).get("context_label"))
    if context_label == "Sources":
        ok("Marketing team ui-schema context_label == Sources")
    else:
        fail("Marketing team ui-schema context_label", f"got {context_label!r}")


def test_investor_template_exists() -> None:
    print("\n── GET /api/templates/investor-research ──────────────────")
    r = get("/api/templates/investor-research")
    if r.status_code != 200:
        fail("GET /api/templates/investor-research 200", f"got {r.status_code}")
        return
    ok("GET /api/templates/investor-research returns 200")
    payload: dict = r.json()
    if isinstance(payload.get("ui_schema"), dict):
        ok("investor-research template returns ui_schema")
    else:
        fail("investor-research ui_schema present", f"got {payload.get('ui_schema')!r}")


def test_investor_ui_schema_differs() -> None:
    print("\n── Multi-template ui-schema comparison ───────────────────")
    content = get("/api/templates/content-engine")
    investor = get("/api/templates/investor-research")
    if content.status_code != 200 or investor.status_code != 200:
        fail(
            "Template comparison endpoints reachable",
            f"content={content.status_code}, investor={investor.status_code}",
        )
        return
    content_schema = (content.json().get("ui_schema") or {}).get("review_page") or {}
    investor_schema = (investor.json().get("ui_schema") or {}).get("review_page") or {}
    content_label = content_schema.get("context_label")
    investor_label = investor_schema.get("context_label")
    if content_label != investor_label:
        ok(f"context_label differs across templates ({content_label!r} vs {investor_label!r})")
    else:
        fail("context_label differs across templates", f"both are {content_label!r}")

    content_dims = [d.get("key") for d in content_schema.get("quality_dimensions") or []]
    investor_dims = [d.get("key") for d in investor_schema.get("quality_dimensions") or []]
    if content_dims != investor_dims:
        ok("quality dimension keys differ across templates")
    else:
        fail("quality dimension keys differ", f"both are {content_dims!r}")


def test_multi_template_review_data() -> None:
    print("\n── Multi-template review dimensions ──────────────────────")
    marketing = get(f"/api/tasks/{DEMO_TASK_ID}/reviews")
    investor = get(f"/api/tasks/{DEMO_INVESTOR_TASK_ID}/reviews")
    if marketing.status_code != 200 or investor.status_code != 200:
        fail(
            "Review endpoints for multi-template comparison",
            f"marketing={marketing.status_code}, investor={investor.status_code}",
        )
        return
    marketing_dims = [d.get("name") for d in (marketing.json().get("dimensions") or [])]
    investor_dims = [d.get("name") for d in (investor.json().get("dimensions") or [])]
    if marketing_dims and investor_dims:
        ok("Both marketing and investor reviews expose dimension lists")
    else:
        fail("Review dimension lists exist", f"marketing={marketing_dims!r}, investor={investor_dims!r}")
        return
    if marketing_dims != investor_dims:
        ok("Marketing and investor review dimensions differ")
    else:
        fail("Marketing and investor review dimensions differ", f"both are {marketing_dims!r}")


def test_approval_flow() -> None:
    """Test full approval flow: get pending → approve → verify gone."""
    print("\n── Approval flow ─────────────────────────────────────────")

    # Get pending approvals
    r = get("/api/approvals")
    if r.status_code != 200:
        fail("Get approvals before approve", f"HTTP {r.status_code}")
        return
    approvals_before: list[dict] = r.json()
    pending_ids_before = {a["id"] for a in approvals_before}

    if not pending_ids_before:
        fail("Pending approvals exist before test", "none found — re-run seed_demo.py")
        return
    ok(f"Found {len(pending_ids_before)} pending approvals before test")

    # Pick the marketing team approval
    mkt_approval = next((a for a in approvals_before if a.get("task_id") == DEMO_TASK_ID), None)
    if not mkt_approval:
        fail("Marketing approval found", f"task {DEMO_TASK_ID!r} not in approvals")
        return
    approval_id = mkt_approval["id"]
    ok(f"Found marketing approval id={approval_id} for task {DEMO_TASK_ID}")

    # POST approve with no edited_content
    r_approve = post(f"/api/approvals/{approval_id}/approve", {"edited_content": None})
    if r_approve.status_code != 200:
        fail("POST /approve 200", f"got {r_approve.status_code}: {r_approve.text[:200]}")
        return
    ok(f"POST /api/approvals/{approval_id}/approve returns 200")

    resp = r_approve.json()
    if resp.get("status") == "approved":
        ok("Approve response.status == 'approved'")
    else:
        fail("Approve response.status", f"got {resp.get('status')!r}")

    if resp.get("edited") is False:
        ok("Approve response.edited == False (no edited_content)")
    else:
        fail("Approve response.edited False", f"got {resp.get('edited')!r}")

    # Verify it's removed from pending queue
    r2 = get("/api/approvals")
    approvals_after: list[dict] = r2.json()
    pending_ids_after = {a["id"] for a in approvals_after}
    if approval_id not in pending_ids_after:
        ok("Approved item removed from pending queue")
    else:
        fail("Approved item removed", f"id {approval_id} still in {pending_ids_after}")


def test_edit_approve_flow() -> None:
    """Test Edit & Approve with edited_content — verifies diff is recorded."""
    print("\n── Edit & Approve flow ───────────────────────────────────")

    # Re-seed to get fresh approvals
    seed_demo()

    r = get(f"/api/approvals?team_id={DEMO_SALES_WS}")
    if r.status_code != 200:
        fail("Get sales approvals", f"HTTP {r.status_code}")
        return
    sales_approvals: list[dict] = r.json()
    if not sales_approvals:
        fail("Sales approval exists", "none found")
        return

    approval_id = sales_approvals[0]["id"]
    ok(f"Found sales approval id={approval_id}")

    edited = "This is the human-edited version of the draft with improvements."
    r_ea = post(f"/api/approvals/{approval_id}/approve", {"edited_content": edited})
    if r_ea.status_code != 200:
        fail("POST /approve with edited_content 200", f"got {r_ea.status_code}: {r_ea.text[:200]}")
        return
    ok("POST /approve with edited_content returns 200")

    resp = r_ea.json()
    if resp.get("edited") is True:
        ok("Response.edited == True when edited_content provided")
    else:
        fail("Response.edited True", f"got {resp.get('edited')!r}")


def test_revise_flow() -> None:
    """Test Request Revision flow."""
    print("\n── Revision flow ─────────────────────────────────────────")

    r = get("/api/approvals")
    approvals: list[dict] = r.json()
    if not approvals:
        fail("Approval exists for revise test", "none found")
        return

    approval_id = approvals[0]["id"]
    feedback = "Strengthen the opening hook with a specific data point."
    r_rv = post(f"/api/approvals/{approval_id}/revise", {"feedback": feedback})

    if r_rv.status_code != 200:
        fail("POST /revise 200", f"got {r_rv.status_code}: {r_rv.text[:200]}")
        return
    ok(f"POST /api/approvals/{approval_id}/revise returns 200")

    resp = r_rv.json()
    if resp.get("status") == "revision_requested":
        ok("Revise response.status == 'revision_requested'")
    else:
        fail("Revise response.status", f"got {resp.get('status')!r}")
    if "task_id" in resp:
        ok(f"Revise response includes task_id: {resp['task_id']}")
    else:
        fail("Revise response has task_id", f"keys: {list(resp.keys())}")


def test_reject_flow() -> None:
    """Test Reject flow."""
    print("\n── Reject flow ───────────────────────────────────────────")

    # Re-seed so we have fresh approvals
    seed_demo()

    r = get("/api/approvals")
    approvals: list[dict] = r.json()
    if not approvals:
        fail("Approval exists for reject test", "none found")
        return

    approval_id = approvals[0]["id"]
    r_rej = post(f"/api/approvals/{approval_id}/reject", {"feedback": "Not aligned with brand voice."})
    if r_rej.status_code != 200:
        fail("POST /reject 200", f"got {r_rej.status_code}: {r_rej.text[:200]}")
        return
    ok(f"POST /api/approvals/{approval_id}/reject returns 200")

    resp = r_rej.json()
    if resp.get("status") == "rejected":
        ok("Reject response.status == 'rejected'")
    else:
        fail("Reject response.status", f"got {resp.get('status')!r}")


def test_create_task() -> None:
    print("\n── POST /api/teams/{id}/tasks ────────────────────────────")
    # Task creation spawns a real agent — use a longer timeout (60s on Intel Mac)
    r = post(f"/api/teams/{DEMO_MKT_WS}/tasks", {
        "title": "e2e test: Research AI coding tools in March 2026",
        "description": "Quick test task for e2e verification",
    }, timeout=90)
    if r.status_code != 200:
        fail("POST /tasks 200", f"got {r.status_code}: {r.text[:300]}")
        return
    ok("POST /tasks returns 200")

    resp: dict = r.json()
    if assert_fields(resp, ["task_id", "team_id", "starting_agent", "spawn"], "TaskCreateResponse shape"):
        ok("TaskCreateResponse has required fields")
    if resp.get("team_id") == DEMO_MKT_WS:
        ok(f"TaskCreateResponse.team_id matches requested team")
    else:
        fail("TaskCreateResponse.team_id matches", f"got {resp.get('team_id')!r}")


def test_approval_id_consistency() -> None:
    """Verify that review.id from /api/tasks/{id}/reviews matches
    the approval.id from /api/approvals for the same task.
    This is the critical mapping the review page relies on."""
    print("\n── Approval ID mapping verification ──────────────────────")

    seed_demo()  # fresh state

    # Get review ID from task endpoint
    r_rev = get(f"/api/tasks/{DEMO_TASK_ID}/reviews")
    if r_rev.status_code != 200:
        fail("Get review for mapping test", f"HTTP {r_rev.status_code}")
        return
    review = r_rev.json()
    review_id = review.get("id")
    ok(f"review.id from GET /api/tasks/{DEMO_TASK_ID}/reviews = {review_id}")

    # Get approval ID for same task from approvals queue
    r_app = get("/api/approvals")
    approvals = r_app.json()
    matching = next((a for a in approvals if a.get("task_id") == DEMO_TASK_ID), None)

    if not matching:
        fail("Approval found for demo task", f"task_id={DEMO_TASK_ID!r} not in approvals")
        return

    approval_id = matching["id"]
    ok(f"approval.id from GET /api/approvals (task_id match) = {approval_id}")

    if review_id == approval_id:
        ok(f"✓ review.id ({review_id}) == approval.id ({approval_id}) — IDs are consistent across endpoints")
    else:
        fail("review.id == approval.id", f"review_id={review_id}, approval_id={approval_id} — page may use wrong ID!")


# ── Main ──────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("APEX E2E Test Suite")
    print("=" * 60)

    print("\n[1/2] Seeding demo data...")
    seed_demo()

    print("\n[2/2] Running tests against http://localhost:8000")

    test_api_health()
    test_get_teams()
    test_get_team_detail()
    test_get_team_members()
    test_get_investor_team_members()
    test_get_team_tasks()
    test_get_investor_team_tasks()
    test_get_approvals()
    test_task_output()
    test_task_evidence()
    test_task_reviews()
    test_task_chain()
    test_investor_task_chain()
    test_get_templates_list()
    test_get_template_detail()
    test_get_team_ui_schema()
    test_investor_template_exists()
    test_investor_ui_schema_differs()
    test_multi_template_review_data()
    test_approval_id_consistency()
    test_approval_flow()
    test_edit_approve_flow()
    test_revise_flow()
    test_reject_flow()
    test_create_task()

    print("\n" + "=" * 60)
    print(f"Results: {PASS} passed, {FAIL} failed")
    print("=" * 60)

    if FAIL > 0:
        print("\nFailed tests:")
        for name, status, detail in results:
            if status == "FAIL":
                print(f"  • {name}: {detail}")
        sys.exit(1)
    else:
        print("\nAll tests passed. ✅")
        sys.exit(0)


if __name__ == "__main__":
    main()
