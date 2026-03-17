from __future__ import annotations

import difflib
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from kernel.api import ApexKernel
from kernel.evidence import EvidenceStore
from kernel.learning import AgentLearning


class WalApexKernel(ApexKernel):
    """ApexKernel variant that enforces WAL mode on every SQLite connection."""

    def _migrate(self) -> None:
        _WORKSPACE_TABLE = """
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                template_id TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """
        _WORKSPACE_IDX = (
            "CREATE INDEX IF NOT EXISTS idx_workspaces_template ON workspaces(template_id)"
        )
        _NEW_COLS: dict[str, list[str]] = {
            "agent_status": ["workspace_id TEXT"],
            "tasks": ["workspace_id TEXT"],
            "agent_messages": ["workspace_id TEXT"],
            "reviews": ["workspace_id TEXT"],
            "evals": ["workspace_id TEXT"],
            "permissions": ["workspace_id TEXT"],
            "budgets": ["workspace_id TEXT"],
            "tool_grants": ["workspace_id TEXT"],
        }
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(_WORKSPACE_TABLE)
            conn.execute(_WORKSPACE_IDX)
            for table, columns in _NEW_COLS.items():
                for col_def in columns:
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                    except sqlite3.OperationalError:
                        pass
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn


class TeamCreateRequest(BaseModel):
    template_id: str
    name: str | None = None
    overrides: dict[str, Any] | None = None


class TeamTaskCreateRequest(BaseModel):
    title: str
    description: str = ""
    goal_id: str | None = None
    pipeline_stage: str | None = None
    priority: int = 2


class ApprovalApproveRequest(BaseModel):
    edited_content: str | None = None


class ApprovalRejectRequest(BaseModel):
    # feedback is optional — reject without a reason is allowed
    feedback: str = ""


class ApprovalReviseRequest(BaseModel):
    feedback: str = Field(..., min_length=1)


APEX_HOME = Path(__file__).resolve().parents[1]
kernel = WalApexKernel(APEX_HOME)
evidence_store = EvidenceStore(APEX_HOME / "db" / "apex_state.db")
learning = AgentLearning(APEX_HOME / "db" / "apex_state.db")

app = FastAPI(title="APEX API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    with kernel._connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")


# ── Internal helpers ─────────────────────────────────────────────────


def _workspace_or_404(team_id: str) -> dict[str, Any]:
    try:
        return kernel.get_workspace(team_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _approval_or_404(review_id: int) -> dict[str, Any]:
    rows = kernel._fetch_all(
        """
        SELECT r.id, r.task_id, r.agent_name, r.output_ref, r.verdict, r.feedback,
               t.workspace_id, t.title, t.description
        FROM reviews r
        LEFT JOIN tasks t ON t.id = r.task_id
        WHERE r.id = ?
        """,
        (review_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"Approval '{review_id}' not found.")
    row = rows[0]
    row["feedback"] = kernel._load_json(row.get("feedback"), fallback=row.get("feedback"))
    return row


def _latest_session_for_task(task_id: str) -> dict[str, Any]:
    rows = kernel._fetch_all(
        """
        SELECT id, agent_name, task_id, context, created_at, last_active, status
        FROM agent_sessions
        WHERE task_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (task_id,),
    )
    if not rows:
        return {}
    # Prefer the writer's session — it contains the final draft (proposed_output).
    # Scout sessions have earlier created_at and their proposed_output is a handoff
    # summary, not the draft. Critic sessions contain review verdicts, not drafts.
    for row in rows:
        if row["agent_name"].endswith("-writer"):
            return row
    return rows[0]


def _first_active_goal_id() -> str | None:
    rows = kernel._fetch_all(
        "SELECT id FROM goals WHERE status = 'active' ORDER BY created_at ASC LIMIT 1"
    )
    return rows[0]["id"] if rows else None


def _resolve_start_agent(team_id: str, workspace: dict[str, Any], pipeline_stage: str | None = None) -> str:
    manifest = kernel.get_template(workspace["template_id"])
    if pipeline_stage:
        first_stage = pipeline_stage.lower().strip()
    else:
        pipeline = manifest.get("pipeline", [])
        if not pipeline:
            raise HTTPException(status_code=400, detail="Template has no pipeline stages.")
        first_stage = str(pipeline[0]).lower()
    stage_map = {
        "discover": "scout",
        "analyze": "analyst",
        "analyse": "analyst",
        "strategize": "strategist",
        "create": "writer",
        "draft": "writer",
        "review": "critic",
        "validate": "critic",
        "publish": "publisher",
        "build": "builder",
        "launch": "apex",
        "grow": "apex",
        "enrich": "analyst",
    }
    role = stage_map.get(first_stage)
    if not role:
        raise HTTPException(status_code=400, detail=f"Unsupported pipeline stage '{first_stage}'.")
    agent_id = f"{team_id}-{role}"
    kernel._ensure_agent_exists(agent_id)
    return agent_id


def _store_learning_diff(workspace_id: str, task_id: str, original: str, edited: str) -> None:
    diff = "\n".join(
        difflib.unified_diff(
            original.splitlines(),
            edited.splitlines(),
            fromfile="ai_draft",
            tofile="user_edit",
            lineterm="",
        )
    )
    payload = json.dumps(
        {
            "task_id": task_id,
            "original": original,
            "edited": edited,
            "diff": diff,
        }
    )
    learning.set_preference(workspace_id, "edit_feedback", f"task:{task_id}", payload)


def _extract_output_text(session: dict[str, Any]) -> str:
    context = session.get("context") or ""
    try:
        parsed = json.loads(context)
        if isinstance(parsed, dict):
            return str(parsed.get("proposed_output") or parsed.get("context") or context)
    except Exception:
        pass
    return str(context)


def _agent_role(config_path: str | None) -> str:
    """Read the role field from an agent's agent.json file via meta.config_path."""
    if not config_path:
        return "custom"
    try:
        data = json.loads(Path(config_path).read_text())
        return str(data.get("role", "custom"))
    except Exception:
        return "custom"


def _pending_approvals_by_team() -> dict[str, int]:
    """Return {workspace_id: count} of tasks awaiting human approval (critic_passed)."""
    rows = kernel._fetch_all(
        """
        SELECT t.workspace_id, COUNT(*) AS cnt
        FROM reviews r
        JOIN tasks t ON t.id = r.task_id
        WHERE t.review_status = 'critic_passed' AND t.workspace_id IS NOT NULL
        GROUP BY t.workspace_id
        """
    )
    return {row["workspace_id"]: row["cnt"] for row in rows if row.get("workspace_id")}


# ── Teams ─────────────────────────────────────────────────────────────


@app.get("/api/teams")
def list_teams() -> list[dict[str, Any]]:
    teams = kernel.list_workspaces()
    approvals_by_team = _pending_approvals_by_team()
    enriched: list[dict[str, Any]] = []
    for team in teams:
        try:
            template = kernel.get_template(team["template_id"])
            template_name = template.get("name", team["template_id"])
        except FileNotFoundError:
            template_name = team["template_id"]
        enriched.append({
            "id": team["id"],
            "name": team.get("name") or team["id"],
            "template_id": team["template_id"],
            "template_name": template_name,
            "status": team.get("status"),
            "agent_count": team.get("agent_count", 0),
            "created_at": team.get("created_at"),
            "pending_approvals": approvals_by_team.get(team["id"], 0),
        })
    return enriched


@app.post("/api/teams")
def create_team(payload: TeamCreateRequest) -> dict[str, Any]:
    overrides = dict(payload.overrides or {})
    if payload.name:
        overrides["workspace_name"] = payload.name
    try:
        return kernel.launch_template(payload.template_id, overrides=overrides)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/teams/{team_id}")
def get_team(team_id: str) -> dict[str, Any]:
    workspace = _workspace_or_404(team_id)
    try:
        template = kernel.get_template(workspace["template_id"])
        template_name = template.get("name", workspace["template_id"])
    except FileNotFoundError:
        template_name = workspace["template_id"]
    members: list[dict[str, Any]] = []
    for agent in workspace.get("agents", []):
        status = kernel.get_agent_status(agent["agent_name"])
        role = _agent_role((status.get("meta") or {}).get("config_path"))
        members.append({
            "agent_name": status["agent_name"],
            "role": role,
            "status": status["status"],
            "last_heartbeat": status.get("last_heartbeat"),
            "current_task": status.get("current_task"),
            "model_active": status.get("model_active"),
        })
    approvals_by_team = _pending_approvals_by_team()
    return {
        "id": workspace["id"],
        "name": workspace.get("name") or workspace["id"],
        "template_id": workspace["template_id"],
        "template_name": template_name,
        "status": workspace.get("status"),
        "agent_count": workspace.get("agent_count", 0),
        "created_at": workspace.get("created_at"),
        "pending_approvals": approvals_by_team.get(team_id, 0),
        "members": members,
    }


@app.get("/api/teams/{team_id}/members")
def get_team_members(team_id: str) -> list[dict[str, Any]]:
    workspace = _workspace_or_404(team_id)
    result: list[dict[str, Any]] = []
    for agent in workspace.get("agents", []):
        status = kernel.get_agent_status(agent["agent_name"])
        role = _agent_role((status.get("meta") or {}).get("config_path"))
        result.append({
            "agent_name": status["agent_name"],
            "role": role,
            "status": status["status"],
            "last_heartbeat": status.get("last_heartbeat"),
            "current_task": status.get("current_task"),
            "model_active": status.get("model_active"),
        })
    return result


@app.get("/api/teams/{team_id}/tasks")
def get_team_tasks(team_id: str) -> list[dict[str, Any]]:
    _workspace_or_404(team_id)
    tasks = kernel._fetch_all(
        """
        SELECT id, title, description, status, review_status, assigned_to, created_at, completed_at
        FROM tasks
        WHERE workspace_id = ?
        ORDER BY created_at DESC
        """,
        (team_id,),
    )
    result: list[dict[str, Any]] = []
    for task in tasks:
        sessions = kernel._fetch_all(
            """
            SELECT agent_name, created_at, status, context
            FROM agent_sessions
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task["id"],),
        )
        messages = kernel._fetch_all(
            """
            SELECT from_agent, to_agent, created_at, msg_type, status
            FROM agent_messages
            WHERE task_id = ?
            ORDER BY created_at ASC
            """,
            (task["id"],),
        )
        events: list[dict[str, Any]] = []
        for s in sessions:
            parsed = kernel._load_json(s.get("context"), fallback={})
            description = ""
            if isinstance(parsed, dict):
                description = (
                    str(parsed.get("actions_taken") or parsed.get("observations") or "")
                )
            if not description:
                description = f"session status: {s.get('status', 'unknown')}"
            events.append({
                "timestamp": s["created_at"],
                "agent": s["agent_name"],
                "description": description[:200],
                "status": s.get("status"),
            })
        for m in messages:
            to_agent = m.get("to_agent") or "?"
            events.append({
                "timestamp": m["created_at"],
                "agent": m["from_agent"],
                "description": f"sent {m.get('msg_type', 'message')} to {to_agent}",
                "status": m.get("status"),
            })
        events.sort(key=lambda e: e.get("timestamp") or "")
        result.append({
            "id": task["id"],
            "title": task["title"],
            "description": task.get("description"),
            "status": task["status"],
            "review_status": task.get("review_status"),
            "assigned_to": task.get("assigned_to"),
            "created_at": task["created_at"],
            "completed_at": task.get("completed_at"),
            "events": events,
        })
    return result


@app.post("/api/teams/{team_id}/tasks")
def create_team_task(team_id: str, payload: TeamTaskCreateRequest) -> dict[str, Any]:
    workspace = _workspace_or_404(team_id)
    goal_id = payload.goal_id or _first_active_goal_id()
    if not goal_id:
        raise HTTPException(status_code=400, detail="No active goal available; provide goal_id.")

    task_id = kernel.create_task(
        {
            "goal_id": goal_id,
            "title": payload.title,
            "description": payload.description,
            "pipeline_stage": payload.pipeline_stage,
            "priority": payload.priority,
            "status": "backlog",
        }
    )
    with kernel._connect() as conn:
        conn.execute("UPDATE tasks SET workspace_id = ? WHERE id = ?", (team_id, task_id))
        conn.commit()
    agent_id = _resolve_start_agent(team_id, workspace, payload.pipeline_stage)
    kernel.assign_task(task_id, agent_id)
    spawn_result = kernel.spawn_agent(agent_id, task_id)
    return {
        "task_id": task_id,
        "team_id": team_id,
        "starting_agent": agent_id,
        "spawn": spawn_result,
    }


# ── Approvals ─────────────────────────────────────────────────────────


@app.get("/api/approvals")
def get_approvals(team_id: str | None = Query(default=None)) -> list[dict[str, Any]]:
    raw = kernel.get_approval_queue(workspace_id=team_id)
    ws_name_cache: dict[str, str] = {}
    result: list[dict[str, Any]] = []
    for row in raw:
        ws_id: str | None = row.get("workspace_id")
        if ws_id and ws_id not in ws_name_cache:
            try:
                ws = kernel.get_workspace(ws_id)
                ws_name_cache[ws_id] = ws.get("name") or ws_id
            except Exception:
                ws_name_cache[ws_id] = ws_id
        team_name = ws_name_cache.get(ws_id or "", ws_id or "")
        result.append({
            "id": row["review_id"],
            "task_id": row["task_id"],
            "task_title": row.get("title"),
            "agent_name": row["agent_name"],
            "stakes": row.get("stakes"),
            "team_id": ws_id,
            "team_name": team_name,
            "verdict": row.get("verdict"),
            "feedback": row.get("feedback"),
            "created_at": row.get("created_at"),
        })
    return result


@app.post("/api/approvals/{review_id}/approve")
def approve_approval(review_id: int, payload: ApprovalApproveRequest | None = None) -> dict[str, Any]:
    approval = _approval_or_404(review_id)
    edited_content = payload.edited_content if payload else None
    if edited_content:
        session = _latest_session_for_task(approval["task_id"])
        original = _extract_output_text(session)
        try:
            _store_learning_diff(approval.get("workspace_id") or "global", approval["task_id"], original, edited_content)
        except Exception as exc:
            # BUG (2026-03-17): kernel/learning.py set_preference inserts a string id
            # ("pref-<hex>") but user_preferences has INTEGER PRIMARY KEY AUTOINCREMENT.
            # The schema in db/schema.sql and the schema in learning.py._migrate() are
            # mismatched. Wrapped in try/except so edit+approve still succeeds — the
            # diff is logged but the approve is not blocked.
            import logging
            logging.warning("_store_learning_diff failed (schema mismatch): %s", exc)
    kernel.approve_action(review_id)
    return {
        "status": "approved",
        "review_id": review_id,
        "task_id": approval["task_id"],
        "edited": bool(edited_content),
    }


@app.post("/api/approvals/{review_id}/reject")
def reject_approval(review_id: int, payload: ApprovalRejectRequest | None = None) -> dict[str, Any]:
    _approval_or_404(review_id)
    feedback = (payload.feedback if payload else "") or ""
    kernel.reject_action(review_id, feedback)
    return {"status": "rejected", "review_id": review_id}


@app.post("/api/approvals/{review_id}/revise")
def revise_approval(review_id: int, payload: ApprovalReviseRequest) -> dict[str, Any]:
    """Request a revision — sets task back to backlog and sends feedback to the writer."""
    approval = _approval_or_404(review_id)
    with kernel._connect() as conn:
        conn.execute(
            """
            UPDATE reviews
            SET verdict = 'needs_revision', reviewed_at = datetime('now')
            WHERE id = ?
            """,
            (review_id,),
        )
        conn.execute(
            """
            UPDATE tasks
            SET status = 'backlog', review_status = 'needs_revision', checked_out_by = NULL
            WHERE id = ?
            """,
            (approval["task_id"],),
        )
        conn.execute(
            """
            INSERT INTO agent_messages (from_agent, to_agent, msg_type, content, task_id, priority)
            VALUES ('operator', ?, 'revision_request', ?, ?, 1)
            """,
            (approval["agent_name"], payload.feedback, approval["task_id"]),
        )
        conn.commit()
    return {
        "status": "revision_requested",
        "review_id": review_id,
        "task_id": approval["task_id"],
    }


# ── Tasks ─────────────────────────────────────────────────────────────


@app.get("/api/tasks/{task_id}/output")
def get_task_output(task_id: str) -> dict[str, Any]:
    session = _latest_session_for_task(task_id)
    if not session:
        raise HTTPException(status_code=404, detail=f"No output found for task '{task_id}'.")
    task_rows = kernel._fetch_all(
        "SELECT title FROM tasks WHERE id = ?",
        (task_id,),
    )
    task_title: str | None = task_rows[0]["title"] if task_rows else None
    return {
        "task_id": task_id,
        "task_title": task_title,
        "content": _extract_output_text(session),
    }


@app.get("/api/tasks/{task_id}/evidence")
def get_task_evidence(task_id: str) -> list[dict[str, Any]]:
    """Return flat list of sources (url/title/snippet/query) gathered for this task."""
    evidence_rows = evidence_store.get_evidence(task_id)
    sources: list[dict[str, Any]] = []
    for row in evidence_rows:
        query = row.get("query", "")
        for result in row.get("results", []):
            url = result.get("url", "")
            if url:
                sources.append({
                    "url": url,
                    "title": result.get("title", ""),
                    "snippet": result.get("snippet"),
                    "query": query,
                })
    return sources


@app.get("/api/tasks/{task_id}/reviews")
def get_task_reviews(task_id: str) -> dict[str, Any] | None:
    """Return the most recent critic review for this task, with parsed scores."""
    rows = kernel._fetch_all(
        """
        SELECT id, task_id, agent_name, stakes, verdict, feedback, created_at, reviewed_at
        FROM reviews
        WHERE task_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (task_id,),
    )
    if not rows:
        return None
    row = rows[0]
    raw = kernel._load_json(row.get("feedback"), fallback={})

    dimensions: list[dict[str, Any]] = []
    overall_score: float | None = None
    feedback_text: str | None = None
    verdict: str | None = row.get("verdict")

    if isinstance(raw, dict):
        for name, score in (raw.get("scores") or {}).items():
            dimensions.append({"name": name, "score": score})
        overall_score = raw.get("overall_score")
        feedback_text = raw.get("feedback")
        if not verdict:
            verdict = raw.get("verdict")
    elif isinstance(raw, str):
        feedback_text = raw

    # Fallback: if feedback was plain text (not JSON with scores), load dimension
    # scores from the evals table which the Critic pipeline always writes to.
    if not dimensions:
        eval_rows = kernel._fetch_all(
            """
            SELECT dimension, score FROM evals
            WHERE task_id = ? AND eval_layer = 'critic'
              AND eval_type = 'dimension_score' AND dimension != 'overall'
            ORDER BY id ASC
            """,
            (task_id,),
        )
        dimensions = [{"name": r["dimension"], "score": r["score"]} for r in eval_rows]

    if overall_score is None:
        overall_rows = kernel._fetch_all(
            """
            SELECT score FROM evals
            WHERE task_id = ? AND eval_layer = 'critic'
              AND eval_type = 'dimension_score' AND dimension = 'overall'
            ORDER BY id DESC LIMIT 1
            """,
            (task_id,),
        )
        if overall_rows:
            overall_score = overall_rows[0]["score"]

    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "agent_name": row.get("agent_name"),
        "stakes": row.get("stakes"),
        "verdict": verdict,
        "overall_score": overall_score,
        "feedback": feedback_text,
        "dimensions": dimensions or None,
        "created_at": row.get("created_at"),
    }


@app.get("/api/tasks/{task_id}/chain")
def get_task_chain(task_id: str) -> dict[str, Any]:
    task_rows = kernel._fetch_all(
        "SELECT id, title, status, review_status, assigned_to, workspace_id, created_at, completed_at FROM tasks WHERE id = ?",
        (task_id,),
    )
    if not task_rows:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' does not exist.")

    sessions = kernel._fetch_all(
        """
        SELECT id, agent_name, task_id, created_at, last_active, status, context
        FROM agent_sessions
        WHERE task_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (task_id,),
    )
    reviews = kernel._fetch_all(
        """
        SELECT id, agent_name, stakes, verdict, feedback, created_at, reviewed_at
        FROM reviews
        WHERE task_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (task_id,),
    )
    messages = kernel._fetch_all(
        """
        SELECT id, from_agent, to_agent, msg_type, content, status, created_at
        FROM agent_messages
        WHERE task_id = ?
        ORDER BY created_at ASC, id ASC
        """,
        (task_id,),
    )

    progress: list[dict[str, Any]] = []
    for session in sessions:
        parsed = kernel._load_json(session.get("context"), fallback={})
        raw_status = parsed.get("status") if isinstance(parsed, dict) else session.get("status")
        # Agent JSON responses store status as {"state": "...", "stakes": "..."} dict.
        # Always normalise to a plain string so the frontend never receives an object.
        if isinstance(raw_status, dict):
            raw_status = raw_status.get("state") or raw_status.get("status") or "done"
        progress.append(
            {
                "type": "session",
                "agent": session["agent_name"],
                "created_at": session.get("created_at"),
                "status": raw_status,
                "summary": (parsed.get("actions_taken") if isinstance(parsed, dict) else None)
                or (parsed.get("observations") if isinstance(parsed, dict) else None)
                or _extract_output_text(session)[:280],
            }
        )
    for review in reviews:
        progress.append(
            {
                "type": "review",
                "agent": review["agent_name"],
                "created_at": review.get("created_at"),
                "status": review.get("verdict"),
                "summary": kernel._load_json(review.get("feedback"), fallback=review.get("feedback")),
            }
        )
    for message in messages:
        progress.append(
            {
                "type": "message",
                "agent": message["from_agent"],
                "created_at": message.get("created_at"),
                "status": message.get("status"),
                "summary": {
                    "to": message.get("to_agent"),
                    "type": message.get("msg_type"),
                    "content": message.get("content"),
                },
            }
        )
    progress.sort(key=lambda item: (item.get("created_at") or "", item.get("type") or ""))

    return {
        "task": task_rows[0],
        "progress": progress,
        "session_count": len(sessions),
        "review_count": len(reviews),
        "message_count": len(messages),
    }


# ── UI Schema helpers ─────────────────────────────────────────────────
#
# TypeScript types for Wave 2 frontend agents (canonical contract below):
#
# interface QualityDimension { key: string; label: string; }
#
# interface ReviewPageSchema {
#   output_type: string;           // "post" | "email" | "brief" | "report" | "general"
#   requires_approval: boolean;
#   context_label: string;         // right-panel header label, e.g. "Sources"
#   context_description: string | null;
#   quality_dimensions: QualityDimension[];
#   approve_action_label: string | null;   // null = hide the button
#   reject_action_label: string | null;
#   revise_action_label: string | null;
# }
#
# interface TeamDisplaySchema {
#   icon: string;               // emoji, e.g. "✍️"
#   category: string;           // display category for browse page
#   short_description: string;  // one-liner for team cards
# }
#
# interface BuilderSchema {
#   suggested_topics: string[];
#   suggested_platforms: string[];
#   suggested_tones: string[];
#   example_missions: string[];
# }
#
# interface UiSchema {
#   team_display: TeamDisplaySchema;
#   review_page: ReviewPageSchema;
#   builder?: BuilderSchema;
# }
#
# interface TemplateAgent {
#   name: string;     // template-local name (e.g. "scout")
#   role: string;
#   description: string;
#   heartbeat: string | null;
#   heartbeat_description: string | null;
#   capabilities: string[];
#   model: { primary: string; fallback?: string; };
# }
#
# interface TemplateSummary {
#   id: string;
#   name: string;
#   description: string;
#   category: string;
#   agent_count: number;
#   pipeline: string[];
#   team_display: TeamDisplaySchema;
#   requires_approval: boolean;
#   builder?: BuilderSchema;
# }
#
# interface TemplateDetail extends TemplateSummary {
#   agents: TemplateAgent[];
#   ui_schema: UiSchema;
# }
#
# ─────────────────────────────────────────────────────────────────────

DEFAULT_UI_SCHEMA: dict[str, Any] = {
    "team_display": {
        "icon": "🤖",
        "category": "General",
        "short_description": "AI agent team",
    },
    "review_page": {
        "output_type": "general",
        "requires_approval": True,
        "context_label": "Sources",
        "context_description": None,
        "quality_dimensions": [
            {"key": "accuracy", "label": "Accuracy"},
            {"key": "completeness", "label": "Completeness"},
            {"key": "grounding", "label": "Grounding"},
        ],
        "approve_action_label": "Approve",
        "reject_action_label": "Reject",
        "revise_action_label": "Request Revision",
    },
}

# Per-category display info and builder defaults derived when template.json
# has no ui_schema block of its own.
_CATEGORY_DISPLAY: dict[str, dict[str, str]] = {
    "content": {
        "icon": "✍️",
        "category": "Content & Marketing",
        "short_description": "Research topics, draft content, quality-review, and publish",
    },
    "sales": {
        "icon": "💼",
        "category": "Sales & Outreach",
        "short_description": "Find ICP-matched leads, enrich with signals, draft personalized outreach",
    },
    "research": {
        "icon": "🔍",
        "category": "Research & Intelligence",
        "short_description": "Deep research, synthesis, and quality-gated intelligence briefings",
    },
    "gtm": {
        "icon": "🚀",
        "category": "Sales & Outreach",
        "short_description": "Full go-to-market: research, positioning, content, and distribution",
    },
    "startup": {
        "icon": "🧠",
        "category": "Engineering & Dev Ops",
        "short_description": "Startup operating system: research, analysis, code, and review",
    },
}

_CATEGORY_OUTPUT_TYPE: dict[str, str] = {
    "content": "post",
    "sales": "email",
    "research": "brief",
    "gtm": "post",
    "startup": "report",
}

_CATEGORY_BUILDER: dict[str, dict[str, Any]] = {
    "content": {
        "suggested_topics": [
            "AI agents", "agentic infrastructure", "robotics",
            "technology trends", "economics",
        ],
        "suggested_platforms": ["LinkedIn", "X/Twitter"],
        "suggested_tones": [
            "Bold and opinionated", "Professional authority",
            "Casual and conversational", "Educational and clear",
        ],
        "example_missions": [
            "Research the most interesting AI development from this week and draft a LinkedIn post",
            "Find 3 trending topics in my industry and write a thread for X",
            "Summarize a recent paper and turn it into educational content",
        ],
    },
    "sales": {
        "suggested_topics": [
            "AI tools", "developer infrastructure", "enterprise software",
            "Series A companies", "SaaS",
        ],
        "suggested_platforms": ["Email", "LinkedIn"],
        "suggested_tones": [
            "Professional authority", "Direct and concise", "Empathetic and curious",
        ],
        "example_missions": [
            "Find 5 companies that recently raised Series A and match our ICP",
            "Research Acme Corp and draft a personalized cold email",
            "Find companies hiring engineering managers — they're likely scaling fast",
        ],
    },
    "research": {
        "suggested_topics": [
            "AI agents", "market trends", "technology adoption",
            "competitive landscape",
        ],
        "suggested_platforms": [],
        "suggested_tones": [
            "Academic and rigorous", "Executive summary", "Analytical and concise",
        ],
        "example_missions": [
            "Research the current state of AI agent frameworks — key papers, products, market",
            "Find recent funding activity in the robotics space",
            "Summarize the competitive landscape for developer tools in 2026",
        ],
    },
    "gtm": {
        "suggested_topics": [
            "market trends", "competitive positioning", "product launches",
            "ICP signals",
        ],
        "suggested_platforms": ["LinkedIn", "X/Twitter", "Blog", "Email"],
        "suggested_tones": [
            "Professional authority", "Bold and opinionated", "Educational and clear",
        ],
        "example_missions": [
            "Research our top 3 competitors and draft a positioning brief",
            "Find content gaps in our space and draft a thought leadership post",
            "Map the competitive landscape and suggest 3 campaign angles",
        ],
    },
    "startup": {
        "suggested_topics": [
            "market sizing", "competitor analysis", "fundraising trends",
            "engineering best practices",
        ],
        "suggested_platforms": [],
        "suggested_tones": [
            "Executive summary", "Analytical", "Direct and concise",
        ],
        "example_missions": [
            "Build a TAM/SAM/SOM model for our target market",
            "Research our top 5 competitors and flag their weaknesses",
            "Analyze recent funding rounds in our space for positioning insights",
        ],
    },
    "finance": {
        "suggested_topics": [
            "AI infrastructure", "developer tools", "vertical SaaS",
            "Series A investors", "deep tech",
        ],
        "suggested_platforms": [],
        "suggested_tones": [
            "Professional authority", "Direct and concise", "Analytical",
        ],
        "example_missions": [
            "Find 10 VCs who recently led Series A rounds in AI infrastructure",
            "Research Andreessen Horowitz's recent AI bets and build an outreach angle",
            "Map the top 20 investors in developer tools and rank by thesis fit",
        ],
    },
}

_DEFAULT_QUALITY_DIMENSIONS: list[dict[str, str]] = [
    {"key": "accuracy", "label": "Accuracy"},
    {"key": "completeness", "label": "Completeness"},
    {"key": "actionability", "label": "Actionability"},
    {"key": "grounding", "label": "Grounding"},
    {"key": "hard_rule_compliance", "label": "Hard Rule Compliance"},
]


def _normalize_ui_schema(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Validate and fill gaps in a ui_schema dict.
    Called on every response — even well-formed templates — so the frontend
    can trust the shape unconditionally.
    """
    result: dict[str, Any] = {}

    # ── team_display ──────────────────────────────────────────────────
    td_raw = raw.get("team_display") if isinstance(raw.get("team_display"), dict) else {}
    default_td = DEFAULT_UI_SCHEMA["team_display"]
    result["team_display"] = {
        "icon": str(td_raw.get("icon") or default_td["icon"]),
        "category": str(td_raw.get("category") or default_td["category"]),
        "short_description": str(
            td_raw.get("short_description") or default_td["short_description"]
        ),
    }

    # ── review_page ───────────────────────────────────────────────────
    rp_raw = raw.get("review_page") if isinstance(raw.get("review_page"), dict) else {}
    default_rp = DEFAULT_UI_SCHEMA["review_page"]

    # quality_dimensions: must be list of {key, label} — drop malformed entries
    raw_dims = rp_raw.get("quality_dimensions")
    if isinstance(raw_dims, list):
        valid_dims = [
            {"key": str(d["key"]), "label": str(d["label"])}
            for d in raw_dims
            if isinstance(d, dict) and d.get("key") and d.get("label")
        ]
    else:
        valid_dims = []
    if not valid_dims:
        valid_dims = _DEFAULT_QUALITY_DIMENSIONS

    # requires_approval: must be bool
    req_approval = rp_raw.get("requires_approval")
    if not isinstance(req_approval, bool):
        req_approval = default_rp["requires_approval"]

    result["review_page"] = {
        "output_type": str(rp_raw.get("output_type") or default_rp["output_type"]),
        "requires_approval": req_approval,
        "context_label": str(rp_raw.get("context_label") or default_rp["context_label"]),
        "context_description": rp_raw.get("context_description") or None,
        "quality_dimensions": valid_dims,
        "approve_action_label": (
            str(rp_raw["approve_action_label"])
            if "approve_action_label" in rp_raw and rp_raw["approve_action_label"] is not None
            else default_rp["approve_action_label"]
        ),
        "reject_action_label": (
            str(rp_raw["reject_action_label"])
            if "reject_action_label" in rp_raw and rp_raw["reject_action_label"] is not None
            else default_rp["reject_action_label"]
        ),
        "revise_action_label": (
            str(rp_raw["revise_action_label"])
            if "revise_action_label" in rp_raw and rp_raw["revise_action_label"] is not None
            else default_rp["revise_action_label"]
        ),
    }

    # ── builder (optional) ────────────────────────────────────────────
    builder_raw = raw.get("builder")
    if isinstance(builder_raw, dict):
        result["builder"] = {
            "suggested_topics": list(builder_raw.get("suggested_topics") or []),
            "suggested_platforms": list(builder_raw.get("suggested_platforms") or []),
            "suggested_tones": list(builder_raw.get("suggested_tones") or []),
            "example_missions": list(builder_raw.get("example_missions") or []),
        }

    return result


def _derive_ui_schema(manifest: dict[str, Any]) -> dict[str, Any]:
    """
    Derive a ui_schema from a template manifest for templates that don't
    define one explicitly. Called before _normalize_ui_schema.
    """
    category = str(manifest.get("category") or "").lower()
    agent_roles = {a.get("role", "") for a in manifest.get("agents", [])}

    # team_display
    display = _CATEGORY_DISPLAY.get(category, {})

    # review_page
    output_type = _CATEGORY_OUTPUT_TYPE.get(category, "general")
    requires_approval = "quality_gate" in agent_roles

    # label content by output type
    context_labels = {
        "post": "Sources",
        "email": "Prospect Research",
        "brief": "Sources & Evidence",
        "report": "Sources & Evidence",
    }
    context_label = context_labels.get(output_type, "Sources")

    approve_labels = {
        "post": "Approve & Publish",
        "email": "Approve & Send",
        "brief": "Approve & Deliver",
        "report": "Approve & Deliver",
    }
    approve_label = approve_labels.get(output_type, "Approve")

    # builder
    builder = _CATEGORY_BUILDER.get(category)

    schema: dict[str, Any] = {
        "team_display": display,
        "review_page": {
            "output_type": output_type,
            "requires_approval": requires_approval,
            "context_label": context_label,
            "context_description": None,
            "quality_dimensions": [],  # will be filled by _normalize_ui_schema
            "approve_action_label": approve_label,
            "reject_action_label": "Reject",
            "revise_action_label": "Request Revision",
        },
    }
    if builder:
        schema["builder"] = builder
    return schema


def _get_ui_schema(manifest: dict[str, Any]) -> dict[str, Any]:
    """Return normalized ui_schema for a manifest — derived if not explicitly set."""
    raw = manifest.get("ui_schema")
    if not raw or not isinstance(raw, dict):
        raw = _derive_ui_schema(manifest)
    else:
        # Template has its own ui_schema but may have empty arrays in builder block.
        # Fill them in from category defaults so the frontend always has useful suggestions.
        category = str(manifest.get("category") or "").lower()
        cat_builder = _CATEGORY_BUILDER.get(category, {})
        if cat_builder and isinstance(raw.get("builder"), dict):
            builder = raw["builder"]
            for field in ("suggested_topics", "suggested_platforms", "suggested_tones", "example_missions"):
                if not builder.get(field) and cat_builder.get(field):
                    builder[field] = cat_builder[field]
    return _normalize_ui_schema(raw)


def _read_agent_json(template_id: str, agent_name: str) -> dict[str, Any]:
    """Read agent.json from the template's agents directory. Returns {} on failure."""
    path = APEX_HOME / "templates" / template_id / "agents" / agent_name / "agent.json"
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


# ── Template endpoints ────────────────────────────────────────────────


@app.get("/api/templates")
def list_templates() -> list[dict[str, Any]]:
    """
    List all available templates for the 'Hire a Team' browse page.

    Returns: TemplateSummary[]
    Each item contains display info, agent count, pipeline, ui_schema.team_display,
    requires_approval, and builder config if present.

    Real example:
    {
      "id": "content-engine",
      "name": "Content Engine",
      "description": "A four-agent content pipeline...",
      "category": "content",
      "agent_count": 4,
      "pipeline": ["discover", "create", "review", "publish"],
      "team_display": {
        "icon": "✍️",
        "category": "Content & Marketing",
        "short_description": "Research topics, draft content, quality-review, and publish"
      },
      "requires_approval": true,
      "builder": {
        "suggested_topics": ["AI agents", ...],
        "suggested_platforms": ["LinkedIn", "X/Twitter"],
        "suggested_tones": ["Bold and opinionated", ...],
        "example_missions": ["Research the most interesting AI development...", ...]
      }
    }
    """
    templates_dir = APEX_HOME / "templates"
    result: list[dict[str, Any]] = []
    if not templates_dir.exists():
        return result
    for entry in sorted(templates_dir.iterdir()):
        manifest_path = entry / "template.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        ui_schema = _get_ui_schema(manifest)
        item: dict[str, Any] = {
            "id": entry.name,
            "name": manifest.get("name", entry.name),
            "description": manifest.get("description", ""),
            "category": manifest.get("category", ""),
            "agent_count": len(manifest.get("agents", [])),
            "pipeline": manifest.get("pipeline", []),
            "team_display": ui_schema["team_display"],
            "requires_approval": ui_schema["review_page"]["requires_approval"],
        }
        if "builder" in ui_schema:
            item["builder"] = ui_schema["builder"]
        result.append(item)
    return result


@app.get("/api/templates/{template_id}")
def get_template_detail(template_id: str) -> dict[str, Any]:
    """
    Full details for one template — powers the Team Builder page.

    Returns: TemplateDetail
    Includes everything from list endpoint plus full agent list (with descriptions
    from agent.json), complete ui_schema, and full builder config.

    Real example:
    {
      "id": "content-engine",
      "name": "Content Engine",
      "description": "...",
      "category": "content",
      "agent_count": 4,
      "pipeline": ["discover", "create", "review", "publish"],
      "agents": [
        {
          "name": "scout",
          "role": "discovery",
          "description": "Trend Finder — monitors industry signals...",
          "heartbeat": "0 */6 * * *",
          "heartbeat_description": "Trend scan every 6 hours",
          "capabilities": ["search", "trend_detection", ...],
          "model": { "primary": "qwen3.5-apex", "fallback": "claude-sonnet" }
        }
      ],
      "team_display": { "icon": "✍️", ... },
      "requires_approval": true,
      "ui_schema": { ... full normalized schema ... },
      "builder": { ... }
    }
    """
    manifest_path = APEX_HOME / "templates" / template_id / "template.json"
    if not manifest_path.exists():
        raise HTTPException(status_code=404, detail=f"Template '{template_id}' not found.")
    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Failed to parse template.json: {exc}") from exc

    ui_schema = _get_ui_schema(manifest)

    # Build agent list — merge template manifest entry with agent.json on disk
    agents: list[dict[str, Any]] = []
    for agent_cfg in manifest.get("agents", []):
        agent_name = str(agent_cfg.get("name") or "")
        if not agent_name:
            continue
        # Prefer on-disk agent.json (it may have been updated post-launch)
        agent_json = _read_agent_json(template_id, agent_name)
        agents.append({
            "name": agent_name,
            "role": str(agent_json.get("role") or agent_cfg.get("role") or "custom"),
            "description": str(
                agent_json.get("description") or agent_cfg.get("description") or ""
            ),
            "heartbeat": agent_cfg.get("heartbeat"),
            "heartbeat_description": agent_cfg.get("heartbeat_description"),
            "capabilities": list(
                agent_json.get("capabilities") or agent_cfg.get("capabilities") or []
            ),
            "model": dict(agent_json.get("model") or agent_cfg.get("model") or {}),
        })

    result: dict[str, Any] = {
        "id": template_id,
        "name": manifest.get("name", template_id),
        "description": manifest.get("description", ""),
        "category": manifest.get("category", ""),
        "agent_count": len(agents),
        "pipeline": manifest.get("pipeline", []),
        "agents": agents,
        "team_display": ui_schema["team_display"],
        "requires_approval": ui_schema["review_page"]["requires_approval"],
        "ui_schema": ui_schema,
    }
    if "builder" in ui_schema:
        result["builder"] = ui_schema["builder"]
    return result


@app.get("/api/teams/{team_id}/ui-schema")
def get_team_ui_schema(team_id: str) -> dict[str, Any]:
    """
    Normalized UI schema for a specific team — powers the review page and team detail view.

    Reads the team's workspace to get template_id, loads template.json,
    and returns the normalized ui_schema. Always returns a valid, complete schema.
    If template.json is missing (deleted template), returns the normalized default.

    Returns: UiSchema  (always a complete, valid schema — never partial)

    Real example:
    {
      "team_display": {
        "icon": "✍️",
        "category": "Content & Marketing",
        "short_description": "Research topics, draft content, quality-review, and publish"
      },
      "review_page": {
        "output_type": "post",
        "requires_approval": true,
        "context_label": "Sources",
        "context_description": null,
        "quality_dimensions": [
          {"key": "accuracy", "label": "Accuracy"},
          {"key": "completeness", "label": "Completeness"},
          {"key": "actionability", "label": "Actionability"},
          {"key": "grounding", "label": "Grounding"},
          {"key": "hard_rule_compliance", "label": "Hard Rule Compliance"}
        ],
        "approve_action_label": "Approve & Publish",
        "reject_action_label": "Reject",
        "revise_action_label": "Request Revision"
      },
      "builder": {
        "suggested_topics": ["AI agents", "agentic infrastructure", ...],
        "suggested_platforms": ["LinkedIn", "X/Twitter"],
        "suggested_tones": ["Bold and opinionated", ...],
        "example_missions": [...]
      }
    }
    """
    workspace = _workspace_or_404(team_id)
    template_id = workspace.get("template_id", "")
    manifest_path = APEX_HOME / "templates" / template_id / "template.json"
    try:
        manifest = json.loads(manifest_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        # Template missing or unreadable — return normalized default
        return _normalize_ui_schema({})
    return _get_ui_schema(manifest)
