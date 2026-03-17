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
        LIMIT 1
        """,
        (task_id,),
    )
    return rows[0] if rows else {}


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
        _store_learning_diff(approval.get("workspace_id") or "global", approval["task_id"], original, edited_content)
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
        progress.append(
            {
                "type": "session",
                "agent": session["agent_name"],
                "created_at": session.get("created_at"),
                "status": parsed.get("status") if isinstance(parsed, dict) else session.get("status"),
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
