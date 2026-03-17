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

try:
    from kernel.display_names import DisplayNameResolver
except ImportError:  # pragma: no cover - optional helper may not exist yet
    DisplayNameResolver = None  # type: ignore[assignment]


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

    recommendation_summary = _build_review_recommendation_summary(
        verdict=verdict,
        feedback_text=feedback_text,
        dimensions=dimensions,
        raw_feedback=raw,
    )

    return {
        "id": row["id"],
        "task_id": row["task_id"],
        "agent_name": row.get("agent_name"),
        "stakes": row.get("stakes"),
        "verdict": verdict,
        "overall_score": overall_score,
        "recommendation_summary": recommendation_summary,
        "feedback": feedback_text,
        "dimensions": dimensions or None,
        "created_at": row.get("created_at"),
    }


_REVIEW_DIMENSION_LABELS = {
    "accuracy": "Accuracy",
    "completeness": "Completeness",
    "actionability": "Actionability",
    "conciseness": "Conciseness",
    "hard_rule_compliance": "Hard rule compliance",
    "grounding": "Grounding",
    "evidence_grounding": "Evidence grounding",
    "authenticity": "Authenticity",
    "relevance": "Relevance",
}

_REVIEW_DIMENSION_DESCRIPTIONS = {
    "accuracy": "check the factual claims against the cited sources",
    "completeness": "consider adding a concrete example",
    "actionability": "make the next step more explicit",
    "conciseness": "tighten repetition and trim filler",
    "hard_rule_compliance": "check the output against the non-negotiable rules",
    "grounding": "tie more claims directly to verified sources",
    "evidence_grounding": "verify every cited source before sending this forward",
    "authenticity": "rewrite in a more natural human voice",
    "relevance": "tighten the fit to the task and audience",
}

_REVIEW_VERDICT_ACTIONS = {
    "PASS": "recommends approval",
    "REVISE": "flagged issues",
    "BLOCK": "blocked this output",
}


def _get_critic_display_name() -> str:
    fallback = "Your Quality Editor"
    if DisplayNameResolver is None:
        return fallback
    try:
        return str(DisplayNameResolver.get_critic_display_name()).strip() or fallback
    except TypeError:
        try:
            return str(DisplayNameResolver().get_critic_display_name()).strip() or fallback
        except Exception:
            return fallback
    except Exception:
        return fallback


def _first_sentence(text: str | None) -> str:
    if not text:
        return ""
    normalized = " ".join(str(text).strip().split())
    if not normalized:
        return ""
    for marker in (". ", "! ", "? "):
        if marker in normalized:
            head = normalized.split(marker, 1)[0].strip()
            return head + marker.strip()
    if normalized[-1] not in ".!?":
        return normalized + "."
    return normalized


def _format_review_score(score: Any) -> str:
    try:
        return f"{float(score):.1f}"
    except (TypeError, ValueError):
        return "0.0"


def _build_review_recommendation_summary(
    verdict: str | None,
    feedback_text: str | None,
    dimensions: list[dict[str, Any]],
    raw_feedback: Any,
) -> str:
    display_name = _get_critic_display_name().rstrip(".")
    action = _REVIEW_VERDICT_ACTIONS.get((verdict or "").upper(), "shared feedback")
    sentences: list[str] = [f"{display_name} {action}."]

    feedback_sentence = _first_sentence(feedback_text)
    if feedback_sentence:
        sentences.append(feedback_sentence)

    dimension_descriptions: dict[str, str] = {}
    if isinstance(raw_feedback, dict):
        maybe_descriptions = raw_feedback.get("dimension_descriptions")
        if isinstance(maybe_descriptions, dict):
            dimension_descriptions = {
                str(key): str(value).strip()
                for key, value in maybe_descriptions.items()
                if value
            }

    scored_dimensions = []
    for dim in dimensions:
        try:
            score_value = float(dim.get("score"))
        except (TypeError, ValueError):
            continue
        scored_dimensions.append((str(dim.get("name") or ""), score_value))

    if scored_dimensions:
        weakest_name, weakest_score = min(scored_dimensions, key=lambda item: item[1])
        if weakest_name and weakest_score < 4.0:
            label = _REVIEW_DIMENSION_LABELS.get(
                weakest_name,
                weakest_name.replace("_", " ").strip().title(),
            )
            description = (
                dimension_descriptions.get(weakest_name)
                or _REVIEW_DIMENSION_DESCRIPTIONS.get(weakest_name)
            )
            weakest_sentence = f"{label} scored {_format_review_score(weakest_score)}/5"
            if description:
                weakest_sentence += f" — {description}"
            if not weakest_sentence.endswith("."):
                weakest_sentence += "."
            sentences.append(weakest_sentence)

    return " ".join(part.strip() for part in sentences if part and part.strip())


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

# ── Team progress endpoint ───────────────────────────────────────────

from datetime import datetime, timezone

_PROGRESS_ICON_BY_SUFFIX = {
    "scout": "🔭",
    "writer": "✍️",
    "analyst": "📊",
    "builder": "🔨",
    "critic": "🛡️",
    "scheduler": "📅",
    "strategist": "🧭",
    "publisher": "📣",
    "apex": "🧠",
}

_PROGRESS_ROLE_DESC_BY_SUFFIX = {
    "scout": "Finds trending topics and industry news",
    "writer": "Drafts posts matched to your voice",
    "analyst": "Analyzes findings and ranks opportunities",
    "builder": "Builds and ships the output",
    "critic": "Reviews accuracy and tone",
    "scheduler": "Plans timing and cross-posting",
    "strategist": "Turns research into positioning and outreach angles",
    "publisher": "Prepares publishing and distribution",
    "apex": "Coordinates the team",
}

_PROGRESS_STATUS_BY_AGENT = {
    "active": "active",
    "drafting": "active",
    "reviewing": "active",
    "blocked": "blocked",
    "paused": "paused",
    "idle": "waiting",
}


def _progress_suffix(agent_name: str) -> str:
    return str(agent_name).rsplit("-", 1)[-1].lower()


def _progress_display_info(agent_name: str, template_id: str) -> dict[str, str]:
    suffix = _progress_suffix(agent_name)
    display_name = suffix.replace("_", " ").title()
    icon = _PROGRESS_ICON_BY_SUFFIX.get(suffix, "🤖")
    role_description = _PROGRESS_ROLE_DESC_BY_SUFFIX.get(suffix, "Helps move the mission forward")

    agent_json = _read_agent_json(template_id, suffix)
    if agent_json.get("description"):
        role_description = str(agent_json["description"])

    try:
        import kernel.display_names as display_names  # type: ignore

        for fn_name in ("get_agent_display", "display_for_agent"):
            fn = getattr(display_names, fn_name, None)
            if callable(fn):
                payload = fn(agent_name)
                if isinstance(payload, dict):
                    display_name = str(payload.get("display_name") or display_name)
                    role_description = str(payload.get("role_description") or role_description)
                    icon = str(payload.get("icon") or icon)
                    return {
                        "display_name": display_name,
                        "role_description": role_description,
                        "icon": icon,
                    }

        for fn_name in ("get_agent_display_name", "display_name_for_agent"):
            fn = getattr(display_names, fn_name, None)
            if callable(fn):
                value = fn(agent_name)
                if value:
                    display_name = str(value)
                    break

        for fn_name in ("get_agent_role_description", "role_description_for_agent"):
            fn = getattr(display_names, fn_name, None)
            if callable(fn):
                value = fn(agent_name)
                if value:
                    role_description = str(value)
                    break

        for fn_name in ("get_agent_icon", "icon_for_agent"):
            fn = getattr(display_names, fn_name, None)
            if callable(fn):
                value = fn(agent_name)
                if value:
                    icon = str(value)
                    break
    except Exception:
        pass

    return {
        "display_name": display_name,
        "role_description": role_description,
        "icon": icon,
    }


def _progress_parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _progress_relative_time(raw: str | None) -> str:
    dt = _progress_parse_ts(raw)
    if dt is None:
        return "just now"
    delta = datetime.now(timezone.utc) - dt
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hr ago"
    days = hours // 24
    return f"{days} day{'s' if days != 1 else ''} ago"


def _progress_rewrite_message(
    *,
    agent_name: str,
    target_name: str | None = None,
    msg_type: str | None = None,
    content: str | None = None,
    template_id: str,
    source: str,
) -> str:
    info = _progress_display_info(agent_name, template_id)
    actor = info["display_name"]
    target = _progress_display_info(target_name or "", template_id)["display_name"] if target_name else "another teammate"
    text = str(content or "").strip()
    lower = text.lower()
    kind = str(msg_type or "").lower()

    if source == "message":
        if kind in {"research_handoff", "handoff", "request"}:
            return f"Handed research to {target}"
        if kind == "review_request":
            return f"{actor} sent work to {target} for review"
        if kind in {"revision_request", "review_feedback"} or "revision" in lower:
            return f"{actor} requested revisions"
        if "sent request to" in lower:
            return f"Handed research to {target}"
        return f"{actor} sent an update"

    if source == "session":
        if "found" in lower and "investor" in lower:
            return f"{actor} found potential investors with fresh sources"
        if "found" in lower and "topic" in lower:
            return f"{actor} found trending topics"
        if "rank" in lower or "tier" in lower:
            return f"{actor} ranked the top opportunities"
        if "draft" in lower and "linkedin" in lower:
            return f"{actor} drafted a LinkedIn post"
        if "draft" in lower and "email" in lower:
            return f"{actor} drafted outreach emails"
        if "outreach" in lower or "cold email" in lower:
            return f"{actor} prepared outreach angles and draft emails"
        if "review" in lower:
            return f"{actor} is reviewing the draft"
        return f"{actor} updated the task"

    return f"{actor} updated the task"


@app.get("/api/teams/{team_id}/progress")
def get_team_progress(team_id: str) -> dict[str, Any]:
    workspace = _workspace_or_404(team_id)
    template_id = workspace["template_id"]

    tasks = kernel._fetch_all(
        """
        SELECT id, title, description, status, review_status, assigned_to, created_at
        FROM tasks
        WHERE workspace_id = ?
        ORDER BY created_at DESC, id DESC
        """,
        (team_id,),
    )
    tasks = [
        t for t in tasks
        if not str(t.get("title") or "").lower().startswith("e2e test:")
        and "e2e verification" not in str(t.get("title") or "").lower()
    ]

    active_task = next((
        t for t in tasks
        if str(t.get("status") or "") in {"in_progress", "active", "review"}
    ), None)
    mission = str((active_task or tasks[0])["title"]) if tasks else ""

    members = kernel._fetch_all(
        """
        SELECT agent_name, status, current_task, last_heartbeat, model_active
        FROM agent_status
        WHERE workspace_id = ?
        ORDER BY agent_name ASC
        """,
        (team_id,),
    )

    session_rows = kernel._fetch_all(
        """
        SELECT s.agent_name, s.task_id, s.created_at AS ts, s.context
        FROM agent_sessions s
        JOIN tasks t ON t.id = s.task_id
        WHERE t.workspace_id = ?
        ORDER BY s.created_at DESC, s.id DESC
        LIMIT 25
        """,
        (team_id,),
    )
    session_agents = {str(r.get("agent_name")) for r in session_rows}

    progress_agents: list[dict[str, Any]] = []
    active_agent_payload: dict[str, Any] | None = None
    for member in members:
        info = _progress_display_info(member["agent_name"], template_id)
        internal_name = str(member["agent_name"])
        normalized_status = _PROGRESS_STATUS_BY_AGENT.get(str(member.get("status") or "idle"), "waiting")
        if normalized_status == "waiting" and internal_name in session_agents:
            normalized_status = "completed"
        payload = {
            "internal_name": internal_name,
            "display_name": info["display_name"],
            "role_description": info["role_description"],
            "status": normalized_status,
            "icon": info["icon"],
        }
        progress_agents.append(payload)
        if str(member.get("status") or "") in {"active", "drafting", "reviewing"}:
            active_agent_payload = payload

    activity_rows: list[dict[str, Any]] = []
    message_rows = kernel._fetch_all(
        """
        SELECT from_agent, to_agent, msg_type, content, created_at AS ts
        FROM agent_messages
        WHERE workspace_id = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 25
        """,
        (team_id,),
    )

    for row in session_rows:
        context = kernel._load_json(row.get("context"), fallback={})
        actions = ""
        if isinstance(context, dict):
            actions = str(context.get("actions_taken") or context.get("proposed_output") or "")
        activity_rows.append({
            "ts": row.get("ts"),
            "message": _progress_rewrite_message(
                agent_name=str(row.get("agent_name") or ""),
                content=actions,
                template_id=template_id,
                source="session",
            ),
            "agent_icon": _progress_display_info(str(row.get("agent_name") or ""), template_id)["icon"],
        })

    for row in message_rows:
        activity_rows.append({
            "ts": row.get("ts"),
            "message": _progress_rewrite_message(
                agent_name=str(row.get("from_agent") or ""),
                target_name=str(row.get("to_agent") or ""),
                msg_type=str(row.get("msg_type") or ""),
                content=str(row.get("content") or ""),
                template_id=template_id,
                source="message",
            ),
            "agent_icon": _progress_display_info(str(row.get("from_agent") or ""), template_id)["icon"],
        })

    activity_rows.sort(key=lambda item: _progress_parse_ts(str(item.get("ts") or "")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    # Remove generic fallback strings that carry no useful information.
    # These are produced when _progress_rewrite_message finds no matching pattern.
    _GENERIC_MESSAGES = {"updated the task", "sent an update"}
    meaningful = [
        item for item in activity_rows
        if not any(item["message"].endswith(g) for g in _GENERIC_MESSAGES)
    ]

    # Deduplicate consecutive runs: if the same agent emits the same message
    # multiple times in a row (e.g. 4 "Scout found trending topics"), keep only
    # the most recent one (list is already sorted newest-first).
    deduped: list[dict[str, Any]] = []
    last_key: tuple[str, str] | None = None
    for item in meaningful:
        key = (item["agent_icon"], item["message"])
        if key != last_key:
            deduped.append(item)
            last_key = key

    recent_activity = [
        {
            "time": _progress_relative_time(str(item.get("ts") or "")),
            "message": item["message"],
            "agent_icon": item["agent_icon"],
        }
        for item in deduped[:10]
    ]

    pending_review_count = sum(
        1 for t in tasks
        if str(t.get("review_status") or "") == "critic_passed"
    )

    if active_agent_payload is not None:
        action = "working"
        name = active_agent_payload["display_name"]
        status_message = f"Your {name} is working..."
        lowered = name.lower()
        if "creator" in lowered or "writer" in lowered:
            status_message = f"Your {name} is drafting..."
        elif "editor" in lowered or "critic" in lowered:
            status_message = f"Your {name} is reviewing..."
        elif "research" in lowered or "scout" in lowered:
            status_message = f"Your {name} is researching..."
        status = "working"
    elif pending_review_count > 0:
        reviewer = next((a for a in progress_agents if a["internal_name"].endswith("-critic")), None)
        reviewer_name = reviewer["display_name"] if reviewer else "team"
        status_message = f"Your {reviewer_name} reviewed the draft — it is ready for you."
        status = "working"
    else:
        status_message = "Your team is idle — send a mission to get started."
        status = "idle"

    try:
        template = kernel.get_template(template_id)
        template_name = str(template.get("name") or template_id)
    except Exception:
        template_name = template_id

    return {
        "team_name": workspace.get("name") or team_id,
        "template_name": template_name,
        "status": status,
        "status_message": status_message,
        "mission": mission,
        "agents": progress_agents,
        "recent_activity": recent_activity,
        "pending_review_count": pending_review_count,
    }


# ── Architect — team recommendation engine ────────────────────────────


class ArchitectRecommendRequest(BaseModel):
    goal: str


_ARCHITECT_ROLE_ICONS: dict[str, str] = {
    "discovery": "🔭",
    "enrichment": "📊",
    "creation": "✍️",
    "outreach": "🎯",
    "quality_gate": "🛡️",
    "publishing_ops": "📅",
    "intelligence": "🔍",
    "orchestrator": "⚡",
    "custom": "🤖",
}

_ARCHITECT_TEMPLATE_META: dict[str, dict[str, str]] = {
    "investor-research": {
        "why": "Your goal involves finding investors — this team specializes in investor discovery, ranking by thesis fit, and drafting personalized outreach for your top targets.",
        "pipeline_summary": "Scout finds → Analyst ranks → Strategist drafts outreach → Critic verifies",
        "constraints_placeholder": "e.g., Only seed-stage funds, focus on AI infrastructure...",
    },
    "content-engine": {
        "why": "Your goal involves content creation — this team researches trending topics, drafts posts matched to your voice, and manages your publishing cadence.",
        "pipeline_summary": "Scout researches → Writer drafts → Critic reviews → Scheduler plans",
        "constraints_placeholder": "e.g., Only write about AI agents, avoid promotional tone...",
    },
    "sales-outreach": {
        "why": "Your goal involves sales outreach — this team finds ICP-matched prospects, enriches each with fresh signals, and drafts personalized cold emails.",
        "pipeline_summary": "Scout finds prospects → Analyst enriches → Writer drafts → Critic verifies",
        "constraints_placeholder": "e.g., B2B SaaS companies, 50–500 employees, Series A stage...",
    },
    "research-assistant": {
        "why": "Your goal involves research — this team searches for evidence, synthesizes findings, and delivers quality-verified intelligence briefings.",
        "pipeline_summary": "Scout searches → Analyst synthesizes → Critic verifies",
        "constraints_placeholder": "e.g., Focus on peer-reviewed sources, include recent 2026 data...",
    },
    "startup-chief-of-staff": {
        "why": "Your goal involves startup operations — this team manages your goals, researches options, and executes across analysis and development tasks.",
        "pipeline_summary": "Apex routes → Scout researches → Analyst synthesizes → Builder executes → Critic reviews",
        "constraints_placeholder": "e.g., Focus on go-to-market strategy, B2B SaaS context...",
    },
    "competitive-intel": {
        "why": "Your goal involves competitive monitoring — this team tracks competitor moves, surfaces market signals, and delivers verified intelligence briefings.",
        "pipeline_summary": "Scout monitors → Analyst flags changes → Critic verifies",
        "constraints_placeholder": "e.g., Focus on pricing changes, product launches, hiring signals...",
    },
    "gtm-engine": {
        "why": "Your goal involves go-to-market — this team covers market research, positioning, content creation, and distribution across channels.",
        "pipeline_summary": "Scout researches → Strategist positions → Writer drafts → Critic reviews → Publisher distributes",
        "constraints_placeholder": "e.g., Focus on developer audience, emphasize technical credibility...",
    },
}

_ARCHITECT_SYNONYMS: dict[str, list[str]] = {
    "investor-research": [
        "investor", "investors", "vc", "vcs", "venture", "fund", "funds",
        "raise", "fundraise", "fundraising", "capital", "pitch", "seed",
        "series", "angel", "angels", "investment",
    ],
    "content-engine": [
        "content", "linkedin", "post", "posts", "tweet", "tweets", "social",
        "media", "publish", "writing", "write", "blog", "newsletter", "draft",
        "marketing", "create",
    ],
    "sales-outreach": [
        "sales", "outreach", "prospect", "prospects", "lead", "leads",
        "email", "cold", "crm", "customer", "customers", "client", "clients",
        "pipeline", "sdr", "selling",
    ],
    "research-assistant": [
        "research", "investigate", "analyze", "analyse", "summarize",
        "brief", "briefing", "intelligence", "report", "study",
        "monitor", "weekly", "digest",
    ],
    "startup-chief-of-staff": [
        "startup", "operations", "ops", "goals", "strategy", "roadmap",
        "chief", "staff", "build", "building",
    ],
    "competitive-intel": [
        "competitor", "competitors", "competitive", "competition", "monitor",
        "monitoring", "market", "intelligence", "tracking", "track",
        "benchmark", "landscape",
    ],
    "gtm-engine": [
        "gtm", "positioning", "messaging", "campaign",
        "launch", "distribution", "channels", "cmo",
    ],
}

import re as _re


def _arch_tokenize(text: str) -> set[str]:
    STOPWORDS = {
        "the", "and", "for", "you", "your", "our", "this", "that", "with",
        "have", "from", "they", "will", "been", "are", "not", "but", "can",
        "all", "any", "get", "help", "need", "want", "like", "also", "into",
        "its", "out", "who", "how", "was", "use", "has", "had", "what",
        "just", "very", "make", "send", "give", "set", "put", "let",
    }
    words = _re.findall(r"[a-z]{3,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


def _arch_template_keywords(template_id: str, manifest: dict[str, Any]) -> set[str]:
    words: set[str] = set()
    words |= _arch_tokenize(manifest.get("name", ""))
    words |= _arch_tokenize(manifest.get("description", ""))
    words |= _arch_tokenize(manifest.get("category", ""))
    ui = manifest.get("ui_schema") or {}
    display = ui.get("team_display") or {}
    words |= _arch_tokenize(display.get("category", ""))
    words |= _arch_tokenize(display.get("short_description", ""))
    for agent in manifest.get("agents", []):
        words |= _arch_tokenize(str(agent.get("role", "")))
        words |= _arch_tokenize(str(agent.get("description", "")))
    for syn in _ARCHITECT_SYNONYMS.get(template_id, []):
        words.add(syn.lower())
    return words


def _arch_agent_preview(manifest: dict[str, Any]) -> list[dict[str, str]]:
    result = []
    for agent_cfg in manifest.get("agents", []):
        agent_name = str(agent_cfg.get("name") or "")
        role = str(agent_cfg.get("role") or "custom")
        icon = _ARCHITECT_ROLE_ICONS.get(role, "🤖")
        label = agent_name.capitalize() if agent_name else role.replace("_", " ").title()
        desc = str(agent_cfg.get("description") or "")
        short = desc.split("—")[0].strip() if "—" in desc else desc.split(".")[0].strip()
        if not short:
            short = role.replace("_", " ").capitalize()
        if len(short) > 60:
            short = short[:57] + "..."
        result.append({"role": label, "description": short, "icon": icon})
    return result


@app.post("/api/architect/recommend")
def architect_recommend(payload: ArchitectRecommendRequest) -> dict[str, Any]:
    """
    V1 keyword-matching team recommender.

    Scores all templates by keyword overlap with the user's goal and returns
    the best match with a team preview and follow-up questions.
    Returns null recommended_template when no confident match is found.

    Request:  { "goal": "string" }
    Response: ArchitectRecommendation
    """
    _NO_MATCH: dict[str, Any] = {
        "recommended_template": None,
        "message": "We're still learning how to help with that. Can you tell us more?",
        "follow_up_questions": [
            {
                "id": "clarify",
                "question": "What kind of outcome are you looking for?",
                "type": "text",
                "placeholder": "e.g., a report, a weekly update, drafted emails...",
            }
        ],
    }

    goal_tokens = _arch_tokenize(payload.goal.strip())
    if not goal_tokens:
        return _NO_MATCH

    templates_dir = APEX_HOME / "templates"
    best_id: str | None = None
    best_score = 0
    best_kw_size = 0
    best_manifest: dict[str, Any] = {}

    if templates_dir.exists():
        for entry in sorted(templates_dir.iterdir()):
            manifest_path = entry / "template.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            kw = _arch_template_keywords(entry.name, manifest)
            score = len(goal_tokens & kw)
            # Prefer higher score; on tie prefer smaller keyword set (more specific template)
            if score > best_score or (score == best_score and score > 0 and len(kw) < best_kw_size):
                best_score = score
                best_kw_size = len(kw)
                best_id = entry.name
                best_manifest = manifest

    if best_score == 0 or best_id is None:
        return _NO_MATCH

    ui_schema = _get_ui_schema(best_manifest)
    display = ui_schema["team_display"]
    meta = _ARCHITECT_TEMPLATE_META.get(best_id, {})

    why = meta.get("why") or (
        f"Your goal matches this team — it specializes in {display['short_description'].lower()}."
    )
    confidence = "high" if best_score >= 2 else "medium"
    agent_preview = _arch_agent_preview(best_manifest)
    pipeline_summary = meta.get("pipeline_summary") or " → ".join(
        a["role"] for a in agent_preview
    )
    constraints_placeholder = meta.get(
        "constraints_placeholder", "e.g., any special requirements or constraints..."
    )

    return {
        "recommended_template": {
            "id": best_id,
            "name": best_manifest.get("name", best_id),
            "description": display["short_description"],
            "icon": display["icon"],
            "match_confidence": confidence,
            "why": why,
        },
        "team_preview": {
            "agents": agent_preview,
            "pipeline_summary": pipeline_summary,
        },
        "follow_up_questions": [
            {
                "id": "autonomy",
                "question": "How hands-on do you want to be?",
                "type": "single_select",
                "options": [
                    {"value": "hands_on", "label": "Review everything before it goes out", "default": True},
                    {"value": "managed", "label": "Only flag issues — auto-approve good work"},
                    {"value": "autopilot", "label": "Run fully on autopilot"},
                ],
            },
            {
                "id": "cadence",
                "question": "How often should we update you?",
                "type": "single_select",
                "options": [
                    {"value": "after_each_step", "label": "After every step", "default": True},
                    {"value": "daily", "label": "Daily summary"},
                    {"value": "on_completion", "label": "Only when something needs your attention"},
                ],
            },
            {
                "id": "constraints",
                "question": "Anything we should know?",
                "type": "text",
                "placeholder": constraints_placeholder,
            },
        ],
    }
