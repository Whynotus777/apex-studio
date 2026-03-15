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

from kernel.api import ApexKernel

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
    return status


def _list_agent_names() -> list[str]:
    rows = kernel._fetch_all("SELECT agent_name FROM agent_status ORDER BY agent_name ASC")
    return [row["agent_name"] for row in rows]


def _workspace_origin(meta: dict[str, Any]) -> str:
    config_path = str(meta.get("config_path", ""))
    marker = "/templates/"
    if marker in config_path:
        tail = config_path.split(marker, 1)[1]
        return tail.split("/", 1)[0]
    return "manual"


async def start_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "APEX Venture Studio online.\n\n"
        "Send any message and I'll route it.\n"
        "Commands:\n"
        "/agents — Agent roster with status and model\n"
        "/status — Alias for /agents\n"
        "/goals — Active goals\n"
        "/tasks — Pending tasks\n"
        "/templates — Available templates\n"
        "/launch <template_id> — Launch a template\n"
        "/workspaces — Active workspaces\n"
        "/rollup — Trigger morning rollup\n"
        "/spawn <agent> — Wake an agent manually"
    )


async def agents_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    lines = ["🤖 *Agents*\n"]
    for agent_id in _list_agent_names():
        agent = _agent_status_summary(agent_id)
        hb = agent.get("last_heartbeat") or "never"
        model = agent.get("model_active") or "unknown"
        lines.append(
            f"{_icon_for_status(agent['status'])} *{agent_id}* — {agent['status']}\n"
            f"    Model: {model}\n"
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
        f"Agents created: {', '.join(result.get('agents_created', [])) or 'none'}",
        f"Permissions applied: {result.get('permissions_applied', 0)}",
        f"Budgets applied: {result.get('budgets_applied', 0)}",
    ]

    keyboard_rows = []
    for agent_id in result.get("agents_created", []):
        keyboard_rows.append(
            [
                InlineKeyboardButton(f"View Status: {agent_id}", callback_data=f"view_status:{agent_id}"),
                InlineKeyboardButton(f"Pause: {agent_id}", callback_data=f"pause_agent:{agent_id}"),
            ]
        )

    reply_markup = InlineKeyboardMarkup(keyboard_rows) if keyboard_rows else None
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def workspaces_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    rows = kernel._fetch_all("SELECT agent_name, status, meta FROM agent_status ORDER BY agent_name ASC")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        meta = json.loads(row["meta"]) if row.get("meta") else {}
        grouped.setdefault(_workspace_origin(meta), []).append(row)

    if not grouped:
        await update.message.reply_text("No active workspaces.")
        return

    lines = ["🗂️ *Workspaces*\n"]
    for workspace_id, agents in sorted(grouped.items()):
        active = sum(1 for agent in agents if agent["status"] == "active")
        lines.append(
            f"*{workspace_id}*\n"
            f"    Agents: {len(agents)} | Active: {active} | Members: {', '.join(a['agent_name'] for a in agents)}"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def goals_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    goals = kernel._fetch_all("SELECT id, name, status FROM goals WHERE status = 'active' ORDER BY created_at DESC")
    lines = ["🎯 *Active Goals*\n"]
    for goal in goals:
        projects = kernel._fetch_all(
            """
            SELECT name, pipeline_stage
            FROM projects
            WHERE goal_id = ? AND status = 'active'
            ORDER BY created_at DESC
            """,
            (goal["id"],),
        )
        lines.append(f"• *{goal['name']}*")
        for project in projects:
            lines.append(f"    └ {project['name']} ({project['pipeline_stage']})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def tasks_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    tasks = [task for task in kernel.get_task_queue() if task["status"] not in ("done", "cancelled")][:15]
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
    """Handle inline button presses (Approve/Reject/Discuss etc.)."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    parts = data.split(":", 1)
    if len(parts) != 2:
        return

    action, value = parts

    if action == "approve":
        await query.edit_message_text(f"Approval by task id is no longer wired here: {value}")
        return

    if action == "reject":
        await query.edit_message_text(f"Reject by task id is no longer wired here: {value}")
        return

    if action == "investigate":
        kernel.send_message("abdul", "analyst", "Investigate this signal further", "directive")
        await query.edit_message_text(f"🔍 Investigating: {value} — routed to Analyst")
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
    app.add_handler(CommandHandler("rollup", rollup_command))
    app.add_handler(CommandHandler("spawn", spawn_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("APEX Telegram bot running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
