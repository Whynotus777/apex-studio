#!/usr/bin/env python3
"""
APEX Telegram Bot — Bidirectional command center.

Inbound: Abdul -> Apex (routing to agents)
Outbound: Agents -> Abdul (via send_message function)

Setup:
1. Create a bot via @BotFather on Telegram
2. Set TELEGRAM_BOT_TOKEN in .env
3. Set TELEGRAM_CHAT_ID in .env (your personal chat ID)
4. Run: python3 adapters/telegram/telegram_bot.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure repo root is on sys.path so `from kernel.api import ...` resolves
# regardless of working directory or PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kernel.api import ApexKernel  # noqa: E402

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
    TELEGRAM_IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - handled at runtime
    Update = Any  # type: ignore[assignment]
    InlineKeyboardButton = Any  # type: ignore[assignment]
    InlineKeyboardMarkup = Any  # type: ignore[assignment]
    Application = Any  # type: ignore[assignment]
    CallbackQueryHandler = Any  # type: ignore[assignment]
    CommandHandler = Any  # type: ignore[assignment]
    MessageHandler = Any  # type: ignore[assignment]
    filters = None  # type: ignore[assignment]
    TELEGRAM_IMPORT_ERROR = exc


def _load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


DEFAULT_HOME = Path(os.environ.get("APEX_HOME", Path(__file__).resolve().parents[2])).resolve()
_load_dotenv(DEFAULT_HOME / ".env")

APEX_HOME = Path(os.environ.get("APEX_HOME", DEFAULT_HOME)).resolve()
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

kernel = ApexKernel(APEX_HOME)

# Pipeline stage → agent role name
_PIPELINE_ROLE_MAP: dict[str, str] = {
    "discover": "scout",
    "analyze": "analyst",
    "analyse": "analyst",
    "build": "builder",
    "validate": "critic",
    "review": "critic",
    "grow": "apex",
    "launch": "apex",
}


def _ensure_runtime_ready() -> None:
    if TELEGRAM_IMPORT_ERROR is not None:
        raise RuntimeError(
            "Install python-telegram-bot: pip install python-telegram-bot --break-system-packages"
        ) from TELEGRAM_IMPORT_ERROR
    if not TOKEN:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN in environment or .env before starting the bot.")


def is_authorized(chat_id: int | str | None) -> bool:
    """Only Abdul can interact with APEX."""
    if not ALLOWED_CHAT_ID:
        return True
    return str(chat_id) == str(ALLOWED_CHAT_ID)


def _truncate(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + "\n\n... (truncated)"


def _icon_for_status(status: str) -> str:
    return {
        "idle": "🟢",
        "active": "🔵",
        "review": "🟡",
        "paused": "⏸️",
        "blocked": "🔴",
        "done": "✅",
    }.get(status, "⬜")


# ── Task-chain helpers ───────────────────────────────────────────────────────

_MAX_CHAIN_HOPS: int = 2

# All role names that can be auto-chained (template names, not workspace-scoped)
_CHAIN_TARGETS: set[str] = {"analyst", "builder", "writer", "critic"}


def _next_chain_agent(workspace_id: str, messages: list[dict[str, Any]]) -> str | None:
    """
    Return the workspace-namespaced agent to auto-spawn next, or None.

    Agent messages use template role names (e.g. 'writer', 'analyst'), not
    workspace-scoped names (e.g. 'ws-abc-writer'). We resolve by checking
    whether {workspace_id}-{role} exists in agent_status.
    """
    for msg in messages:
        target = msg.get("to", "").lower().strip()
        # Strip any workspace prefix so bare role names and scoped names both work
        role = target.split("-")[-1] if "-" in target else target
        if role not in _CHAIN_TARGETS:
            continue
        agent_id = f"{workspace_id}-{role}"
        rows = kernel._fetch_all(
            "SELECT agent_name FROM agent_status WHERE agent_name = ?", (agent_id,)
        )
        if rows:
            return agent_id
    return None


def _fetch_task_evidence(task_id: str) -> list[dict[str, Any]]:
    """Pull evidence rows for a task from the DB."""
    try:
        return kernel._fetch_all(
            "SELECT tool_name, query, results FROM evidence "
            "WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        )
    except Exception:
        return []


def _sources_section(task_id: str) -> str:
    """Build a compact sources block (max 5 unique URLs)."""
    rows = _fetch_task_evidence(task_id)
    seen: set[str] = set()
    entries: list[str] = []
    for row in rows:
        try:
            results = (
                json.loads(row["results"])
                if isinstance(row["results"], str)
                else row["results"]
            )
        except Exception:
            results = []
        for r in results:
            if len(entries) >= 5:
                break
            url = r.get("url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            title = (r.get("title") or url)[:55]
            entries.append(f"• {title}\n  {url}")
        if len(entries) >= 5:
            break
    if not entries:
        return "📎 Sources: (no sources retrieved)"
    return "📎 Sources:\n" + "\n".join(entries)


def _summarise(text: str, limit: int) -> str:
    """Truncate text to `limit` chars, preferring a sentence boundary."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    boundary = cut.rfind(". ")
    if boundary > limit // 2:
        return cut[: boundary + 1]
    return cut.rstrip() + "…"


def _format_task_card(
    agent_id: str,
    result: dict[str, Any],
    task_id: str,
    workspace_id: str,
) -> str:
    """
    Mobile-friendly agent response card.
    Capped at 500 chars; overflow truncated with /workspace link.
    """
    status = result.get("status", {})
    if isinstance(status, dict):
        state = status.get("state", "unknown")
        detail = (status.get("reason") or status.get("stakes") or "").strip()
        status_text = f"{state} {detail}".strip()
    else:
        status_text = str(status)

    actions = (result.get("actions_taken") or "").strip()
    obs = (result.get("observations") or "").strip()
    summary = _summarise(f"{actions} {obs}".strip(), 220)

    sources = _sources_section(task_id)

    lines = [
        f"🤖 *{agent_id}*",
        f"Status: {status_text}",
        "",
        summary,
        "",
        sources,
    ]
    text = "\n".join(lines)
    if len(text) > 500:
        text = text[:450].rstrip() + f"…\n_Full response: /workspace {workspace_id}_"
    return text


def _format_spawn_result(result: dict[str, Any]) -> str:
    status = result.get("status", {})
    if isinstance(status, dict):
        status_text = status.get("state", "unknown")
        detail = status.get("reason") or status.get("stakes")
        if detail:
            status_text = f"{status_text} {detail}"
    else:
        status_text = str(status)

    lines = [
        f"Agent: {result.get('agent_id', 'unknown')}",
        f"Task: {result.get('task_id') or 'heartbeat'}",
        f"Status: {status_text}",
        "",
        f"Actions: {result.get('actions_taken', 'none')}",
        f"Observations: {result.get('observations', 'none')}",
        f"Proposed output: {result.get('proposed_output', 'none')}",
    ]

    messages = result.get("messages", [])
    if messages:
        lines.append("Messages:")
        for message in messages:
            lines.append(f"- {message.get('to')}: [{message.get('type')}] {message.get('content')}")

    stderr = result.get("stderr")
    if stderr:
        lines.extend(["", f"stderr: {stderr}"])

    return "\n".join(lines)


def _agent_status_summary(agent_id: str) -> dict[str, Any]:
    status = kernel.get_agent_status(agent_id)
    meta = status.get("meta", {}) or {}
    status["config_path"] = meta.get("config_path", "")
    # workspace_id lives on the agent_status row (column) and also in meta
    # get_agent_status() doesn't SELECT workspace_id, so pull from meta fallback
    if "workspace_id" not in status or status["workspace_id"] is None:
        status["workspace_id"] = meta.get("workspace_id")
    return status


def _list_agent_names() -> list[str]:
    rows = kernel._fetch_all("SELECT agent_name FROM agent_status ORDER BY agent_name ASC")
    return [row["agent_name"] for row in rows]


def _list_agent_rows(workspace_id: str | None = None) -> list[dict[str, Any]]:
    if workspace_id:
        return kernel._fetch_all(
            "SELECT agent_name FROM agent_status WHERE workspace_id = ? ORDER BY agent_name ASC",
            (workspace_id,),
        )
    return kernel._fetch_all("SELECT agent_name FROM agent_status ORDER BY agent_name ASC")


def _get_or_ensure_inbox_goal() -> str | None:
    """Return the first active goal id, or None if the goals table is empty."""
    rows = kernel._fetch_all(
        "SELECT id FROM goals WHERE status = 'active' ORDER BY created_at ASC LIMIT 1"
    )
    return rows[0]["id"] if rows else None


_INTENT_KEYWORDS: dict[str, list[str]] = {
    "writer":  ["draft", "write", "create", "post", "article", "content"],
    "scout":   ["research", "find", "search", "analyze", "analyse", "investigate", "competitors"],
    "critic":  ["review", "check", "evaluate"],
}


def _resolve_start_agent_by_intent(workspace_id: str, mission: str) -> str | None:
    """
    Route to the most appropriate agent based on intent keywords in the mission.
    Returns workspace-scoped agent id if it exists, else None.
    Falls back to scout if no other keyword matches and scout exists.
    """
    words = mission.lower().split()
    for role, keywords in _INTENT_KEYWORDS.items():
        if any(kw in words for kw in keywords):
            agent_id = f"{workspace_id}-{role}"
            rows = kernel._fetch_all(
                "SELECT agent_name FROM agent_status WHERE agent_name = ?", (agent_id,)
            )
            if rows:
                return agent_id
    # Default: scout
    scout_id = f"{workspace_id}-scout"
    rows = kernel._fetch_all(
        "SELECT agent_name FROM agent_status WHERE agent_name = ?", (scout_id,)
    )
    return scout_id if rows else None


def _resolve_start_agent(workspace_id: str, template_id: str) -> str | None:
    """
    Map the first pipeline stage of the template to a workspace-namespaced agent.
    Returns the agent_id string or None if it cannot be resolved.
    """
    try:
        manifest = kernel.get_template(template_id)
    except FileNotFoundError:
        return None
    pipeline = manifest.get("pipeline", [])
    if not pipeline:
        return None
    first_stage = pipeline[0].lower()
    role = _PIPELINE_ROLE_MAP.get(first_stage)
    if not role:
        return None
    agent_id = f"{workspace_id}-{role}"
    # Verify the agent actually exists
    rows = kernel._fetch_all(
        "SELECT agent_name FROM agent_status WHERE agent_name = ?", (agent_id,)
    )
    return agent_id if rows else None


# ── Commands ────────────────────────────────────────────────────────────────

async def start_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "APEX Venture Studio online.\n\n"
        "Send any message and I'll route it.\n"
        "Commands:\n"
        "/agents [workspace_id] — Agent roster with status and model\n"
        "/status — Alias for /agents\n"
        "/goals — Active goals\n"
        "/tasks — Pending tasks\n"
        "/templates — Available templates\n"
        "/launch <template_id> — Launch a template\n"
        "/workspaces — Active workspaces\n"
        "/workspace <workspace_id> — Workspace detail\n"
        "/task <workspace_id> <mission> — Create and run a task\n"
        "/approvals — Pending approval queue\n"
        "/rollup — Trigger morning rollup\n"
        "/spawn <agent> — Wake an agent manually"
    )


async def agents_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    workspace_id = context.args[0] if getattr(context, "args", None) else None
    agent_rows = _list_agent_rows(workspace_id)
    if not agent_rows:
        msg = f"No agents found for workspace `{workspace_id}`." if workspace_id else "No agents found."
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    heading = f"🤖 *Agents* `{workspace_id}`\n" if workspace_id else "🤖 *Agents*\n"
    lines = [heading]
    for row in agent_rows:
        agent_id = row["agent_name"]
        agent = _agent_status_summary(agent_id)
        hb = agent.get("last_heartbeat") or "never"
        model = agent.get("model_active") or "unknown"
        ws = agent.get("workspace_id") or "global"
        lines.append(
            f"{_icon_for_status(agent['status'])} *{agent_id}* — {agent['status']}\n"
            f"    Model: {model}\n"
            f"    Workspace: {ws}\n"
            f"    Last active: {hb}\n"
            f"    Open tasks: {agent.get('task_count', 0)}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def status_command(update: Update, context: Any) -> None:
    await agents_command(update, context)


async def templates_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    templates = kernel.list_templates()
    if not templates:
        await update.message.reply_text("No templates available.")
        return

    lines = ["📦 *Templates*\n"]
    for template in templates:
        lines.append(
            f"*{template['id']}* — {template['name']}\n"
            f"    {template.get('description', '')}\n"
            f"    Category: {template.get('category', 'unknown')} | Agents: {template.get('agent_count', 0)}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def launch_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /launch <template_id>")
        return

    template_id = args[0]
    try:
        result = kernel.launch_template(template_id)
    except Exception as exc:
        await update.message.reply_text(f"Failed to launch template '{template_id}': {exc}")
        return

    lines = [
        "🚀 *Template Launched*",
        "",
        f"Template: *{result.get('template_name', template_id)}*",
        f"Workspace: `{result.get('workspace_id', 'n/a')}`",
        f"Agents created: {', '.join(result.get('agents_created', [])) or 'none (already running)'}",
        f"Permissions applied: {result.get('permissions_applied', 0)}",
        f"Budgets applied: {result.get('budgets_applied', 0)}",
    ]

    keyboard_rows = []
    for agent_id in result.get("agents_created", []):
        keyboard_rows.append([
            InlineKeyboardButton(f"Status: {agent_id}", callback_data=f"view_status:{agent_id}"),
            InlineKeyboardButton(f"Pause", callback_data=f"pause_agent:{agent_id}"),
        ])

    reply_markup = InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def workspaces_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    all_workspaces = kernel.list_workspaces()
    # Filter out deleted workspaces
    workspaces = [w for w in all_workspaces if w.get("status") != "deleted"]
    if not workspaces:
        await update.message.reply_text("No active workspaces.")
        return

    lines = ["🗂️ *Workspaces*\n"]
    for workspace in workspaces:
        ws_detail = kernel.get_workspace(workspace["id"])
        active = sum(1 for a in ws_detail.get("agents", []) if a["status"] == "active")
        lines.append(
            f"*{workspace['id']}* — {workspace.get('name', workspace['id'])}\n"
            f"    Template: {workspace.get('template_id', 'unknown')} | Status: {workspace.get('status', 'unknown')}\n"
            f"    Agents: {workspace.get('agent_count', 0)} | Active: {active}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def workspace_command(update: Update, context: Any) -> None:
    """Show a detailed summary for a single workspace."""
    if not is_authorized(update.effective_chat.id):
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /workspace <workspace_id>")
        return

    workspace_id = args[0]
    try:
        ws = kernel.get_workspace(workspace_id)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    # Template name
    try:
        manifest = kernel.get_template(ws["template_id"])
        template_name = manifest.get("name", ws["template_id"])
    except FileNotFoundError:
        template_name = ws["template_id"]

    # Agents
    agents = ws.get("agents", [])
    agent_lines = []
    for a in agents:
        agent_lines.append(
            f"  {_icon_for_status(a['status'])} {a['agent_name']} — {a['status']}"
        )

    # Recent tasks assigned to workspace agents
    agent_names = [a["agent_name"] for a in agents]
    recent_tasks: list[dict[str, Any]] = []
    if agent_names:
        placeholders = ",".join("?" for _ in agent_names)
        recent_tasks = kernel._fetch_all(
            f"""
            SELECT title, status, assigned_to
            FROM tasks
            WHERE assigned_to IN ({placeholders})
            ORDER BY created_at DESC
            LIMIT 5
            """,
            agent_names,
        )

    task_lines = []
    for t in recent_tasks:
        task_lines.append(
            f"  {_icon_for_status(t['status'])} {t['title']} → {t.get('assigned_to', 'n/a')}"
        )

    # Pending approvals count
    approval_queue = kernel.get_approval_queue(workspace_id=workspace_id)
    pending_count = len(approval_queue)

    lines = [
        f"🗂️ *Workspace* `{workspace_id}`",
        f"Template: *{template_name}*",
        f"Status: {ws.get('status', 'unknown')}",
        f"Created: {ws.get('created_at', 'n/a')}",
        "",
        f"*Agents* ({len(agents)})",
    ]
    lines.extend(agent_lines or ["  none"])
    lines += ["", f"*Recent Tasks* (last {len(recent_tasks)})"]
    lines.extend(task_lines or ["  none"])
    lines += ["", f"*Pending Approvals:* {pending_count}"]

    keyboard = []
    if pending_count:
        keyboard.append([InlineKeyboardButton(
            f"View {pending_count} approvals", callback_data=f"show_approvals:{workspace_id}"
        )])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def task_command(update: Update, context: Any) -> None:
    """
    /task <workspace_id> <mission text>

    Creates a task in the workspace and spawns the first-pipeline-stage agent.
    """
    if not is_authorized(update.effective_chat.id):
        return

    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Usage: /task <workspace_id> <mission>\n"
            "Example: /task ws-abc123 Analyze the PE deal landscape"
        )
        return

    workspace_id = args[0]
    mission = " ".join(args[1:])

    # Validate workspace
    try:
        ws = kernel.get_workspace(workspace_id)
    except ValueError:
        await update.message.reply_text(f"Workspace `{workspace_id}` not found.", parse_mode="Markdown")
        return

    if ws.get("status") == "deleted":
        await update.message.reply_text(f"Workspace `{workspace_id}` has been deleted.", parse_mode="Markdown")
        return

    # Find a goal to attach the task to
    goal_id = _get_or_ensure_inbox_goal()
    if not goal_id:
        await update.message.reply_text(
            "No active goals found. Create a goal first:\n"
            "Use the DB directly or seed the database."
        )
        return

    # Create the task
    try:
        task_id = kernel.create_task({
            "goal_id": goal_id,
            "title": mission,
            "description": f"Workspace: {workspace_id}\nIssued via Telegram.",
            "status": "backlog",
        })
    except Exception as exc:
        await update.message.reply_text(f"Failed to create task: {exc}")
        return

    # Determine starting agent: keyword routing first, then pipeline fallback
    agent_id = _resolve_start_agent_by_intent(workspace_id, mission) \
        or _resolve_start_agent(workspace_id, ws["template_id"])
    if not agent_id:
        await update.message.reply_text(
            f"Task `{task_id}` created but could not resolve a starting agent "
            f"for template `{ws['template_id']}`.\n"
            f"Use /spawn to run an agent manually.",
            parse_mode="Markdown",
        )
        return

    # Assign and spawn
    try:
        kernel.assign_task(task_id, agent_id)
    except Exception as exc:
        await update.message.reply_text(f"Task created (`{task_id}`) but assignment failed: {exc}")
        return

    await update.message.reply_text(
        f"📋 Task `{task_id}` created\n"
        f"Mission: {mission}\n"
        f"Starting: `{agent_id}`\n\n"
        "🚀 Spawning agent…",
        parse_mode="Markdown",
    )

    current_agent = agent_id
    for hop in range(1, _MAX_CHAIN_HOPS + 1):
        try:
            response = kernel.spawn_agent(current_agent, task_id)
        except Exception as exc:
            await update.message.reply_text(f"Agent spawn failed ({current_agent}): {exc}")
            break

        await update.message.reply_text(
            _format_task_card(current_agent, response, task_id, workspace_id),
            parse_mode="Markdown",
        )

        # Auto-chain: follow messages to analyst/builder (capped at MAX_CHAIN_HOPS)
        if hop < _MAX_CHAIN_HOPS:
            next_agent = _next_chain_agent(workspace_id, response.get("messages", []))
            if next_agent:
                await update.message.reply_text(
                    f"🔗 Chaining to `{next_agent}`…", parse_mode="Markdown"
                )
                current_agent = next_agent
                continue
        break


async def approvals_command(update: Update, context: Any) -> None:
    """Show the pending approval queue with Approve/Reject inline buttons."""
    if not is_authorized(update.effective_chat.id):
        return

    queue = kernel.get_approval_queue()
    if not queue:
        await update.message.reply_text("✅ No items pending approval.")
        return

    await update.message.reply_text(f"📥 *Approval Queue* — {len(queue)} item(s)", parse_mode="Markdown")

    for item in queue[:10]:  # cap at 10 to avoid spam
        review_id = item["review_id"]
        agent = item.get("agent_name", "unknown")
        title = item.get("title") or item.get("task_id") or "untitled"
        stakes = item.get("stakes", "low").upper()
        feedback = item.get("feedback") or ""
        ws = item.get("workspace_id") or "global"

        stakes_icon = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(stakes, "⬜")

        text = (
            f"{stakes_icon} *{title}*\n"
            f"Agent: {agent} | Workspace: {ws}\n"
            f"Stakes: {stakes}"
        )
        if feedback:
            text += f"\nCritic note: {feedback[:200]}"

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"approve_review:{review_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_review:{review_id}"),
        ]])

        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def goals_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    goals = kernel._fetch_all("SELECT id, name, status FROM goals WHERE status = 'active' ORDER BY created_at DESC")
    lines = ["🎯 *Active Goals*\n"]
    for goal in goals:
        projects = kernel._fetch_all(
            "SELECT name, pipeline_stage FROM projects WHERE goal_id = ? AND status = 'active' ORDER BY created_at DESC",
            (goal["id"],),
        )
        lines.append(f"• *{goal['name']}*")
        for project in projects:
            lines.append(f"    └ {project['name']} ({project['pipeline_stage']})")
    if len(lines) == 1:
        lines.append("No active goals.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def tasks_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    tasks = [t for t in kernel.get_task_queue() if t["status"] not in ("done", "cancelled")][:15]
    if not tasks:
        await update.message.reply_text("No pending tasks.")
        return

    lines = ["📋 *Pending Tasks*\n"]
    for task in tasks:
        agent = task.get("assigned_to") or "unassigned"
        lines.append(
            f"{_icon_for_status(task['status'])} *{task['title']}*\n"
            f"    {task.get('project_name') or 'No project'} | {task.get('pipeline_stage') or 'n/a'} | {agent}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def rollup_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text("☀️ Triggering morning rollup...")
    response = kernel.spawn_agent("apex")
    await update.message.reply_text(_truncate(_format_spawn_result(response)))


async def spawn_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /spawn <agent_name> [task_id]")
        return

    agent_name = args[0]
    task_id = args[1] if len(args) > 1 else None
    valid_agents = _list_agent_names()
    if agent_name not in valid_agents:
        await update.message.reply_text(f"Unknown agent: {agent_name}\nAvailable: {', '.join(valid_agents)}")
        return

    await update.message.reply_text(f"🚀 Spawning {agent_name}...")
    response = kernel.spawn_agent(agent_name, task_id)
    await update.message.reply_text(_truncate(_format_spawn_result(response)))


async def handle_message(update: Update, context: Any) -> None:
    """Route free-text messages through Apex."""
    if not is_authorized(update.effective_chat.id):
        return

    text = update.message.text
    if not text:
        return

    await update.message.reply_text("📨 Routing to Apex...")
    response = kernel.route_user_message(text)
    await update.message.reply_text(_truncate(_format_spawn_result(response)))


async def handle_callback(update: Update, context: Any) -> None:
    """Handle inline button presses."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    parts = data.split(":", 1)
    if len(parts) != 2:
        return

    action, value = parts

    if action == "approve_review":
        try:
            kernel.approve_action(int(value))
            await query.edit_message_text(f"✅ Approved review #{value}")
        except Exception as exc:
            await query.message.reply_text(f"Approval failed: {exc}")
        return

    if action == "reject_review":
        try:
            kernel.reject_action(int(value), "Rejected via Telegram")
            await query.edit_message_text(f"❌ Rejected review #{value}")
        except Exception as exc:
            await query.message.reply_text(f"Rejection failed: {exc}")
        return

    if action == "investigate":
        kernel.send_message("abdul", "analyst", "Investigate this signal further", "directive")
        await query.edit_message_text(f"🔍 Investigating: {value} — routed to Analyst")
        return

    if action == "show_approvals":
        queue = kernel.get_approval_queue(workspace_id=value)
        if not queue:
            await query.message.reply_text(f"No pending approvals for `{value}`.", parse_mode="Markdown")
            return
        for item in queue[:5]:
            review_id = item["review_id"]
            title = item.get("title") or item.get("task_id") or "untitled"
            text = f"📥 *{title}* — stakes: {item.get('stakes', 'low').upper()}"
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_review:{review_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_review:{review_id}"),
            ]])
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    if action == "view_status":
        try:
            status = _agent_status_summary(value)
        except Exception as exc:
            await query.message.reply_text(f"Failed to load status for {value}: {exc}")
            return
        text = (
            f"🤖 *{value}*\n"
            f"Status: {status['status']}\n"
            f"Model: {status.get('model_active') or 'unknown'}\n"
            f"Workspace: {status.get('workspace_id') or 'global'}\n"
            f"Last active: {status.get('last_heartbeat') or 'never'}\n"
            f"Open tasks: {status.get('task_count', 0)}"
        )
        await query.message.reply_text(text, parse_mode="Markdown")
        return

    if action == "pause_agent":
        try:
            kernel.pause_agent(value)
        except Exception as exc:
            await query.message.reply_text(f"Failed to pause {value}: {exc}")
            return
        await query.message.reply_text(f"⏸️ Paused agent: {value}")


def send_to_abdul(text: str, buttons: list[list[dict[str, str]]] | None = None) -> None:
    """Outbound: Agent -> Abdul."""
    import requests

    if not TOKEN or not ALLOWED_CHAT_ID:
        print(f"[outbound] {text}")
        return

    payload: dict[str, Any] = {"chat_id": ALLOWED_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}

    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json=payload)


def main() -> None:
    _ensure_runtime_ready()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("agents", agents_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("goals", goals_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("templates", templates_command))
    app.add_handler(CommandHandler("launch", launch_command))
    app.add_handler(CommandHandler("workspaces", workspaces_command))
    app.add_handler(CommandHandler("workspace", workspace_command))
    app.add_handler(CommandHandler("task", task_command))
    app.add_handler(CommandHandler("approvals", approvals_command))
    app.add_handler(CommandHandler("rollup", rollup_command))
    app.add_handler(CommandHandler("spawn", spawn_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("APEX Telegram bot running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
