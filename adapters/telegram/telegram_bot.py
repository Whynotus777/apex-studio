#!/usr/bin/env python3
"""
APEX Telegram Bot — Bidirectional command center.

Inbound: Abdul → Apex (routing to agents)
Outbound: Agents → Abdul (via send_message function)

Setup:
1. Create a bot via @BotFather on Telegram
2. Set TELEGRAM_BOT_TOKEN in .env
3. Set TELEGRAM_CHAT_ID in .env (your personal chat ID)
4. Run: python3 adapters/telegram/telegram_bot.py
"""

import os
import sys
import json
import sqlite3
import subprocess
import asyncio
import signal
from pathlib import Path
from datetime import datetime

try:
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters
except ImportError:
    print("Install python-telegram-bot: pip install python-telegram-bot --break-system-packages")
    sys.exit(1)

# Config
APEX_HOME = os.environ.get("APEX_HOME", os.path.expanduser("~/apex-studio"))
DB_PATH = os.path.join(APEX_HOME, "db", "apex_state.db")
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

if not TOKEN:
    print("ERROR: Set TELEGRAM_BOT_TOKEN in environment or .env file")
    sys.exit(1)


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


def spawn_agent(agent_name, task_id=None):
    """Spawn an agent and return its response."""
    cmd = [os.path.join(APEX_HOME, "kernel", "spawn-agent.sh"), agent_name]
    if task_id:
        cmd.append(task_id)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
            env={**os.environ, "APEX_HOME": APEX_HOME}
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        return f"ERROR: Agent {agent_name} timed out after 120s"
    except Exception as e:
        return f"ERROR: Failed to spawn {agent_name}: {e}"


def is_authorized(chat_id):
    """Only Abdul can interact with APEX."""
    if not ALLOWED_CHAT_ID:
        return True  # No restriction set
    return str(chat_id) == str(ALLOWED_CHAT_ID)


# --- Handlers ---

async def start_command(update: Update, context):
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text(
        "APEX Venture Studio online.\n\n"
        "Send any message and I'll route it.\n"
        "Commands:\n"
        "/status — Agent status overview\n"
        "/goals — Active goals\n"
        "/tasks — Pending tasks\n"
        "/rollup — Trigger morning rollup\n"
        "/spawn <agent> — Wake an agent manually"
    )


async def status_command(update: Update, context):
    if not is_authorized(update.effective_chat.id):
        return
    agents = db_query("SELECT agent_name, status, model_active, last_heartbeat FROM agent_status")
    lines = ["📊 *Agent Status*\n"]
    for a in agents:
        icon = "🟢" if a["status"] == "idle" else "🔵" if a["status"] == "active" else "🔴"
        hb = a["last_heartbeat"] or "never"
        lines.append(f"{icon} *{a['agent_name']}* — {a['status']} ({a['model_active']})\n    Last active: {hb}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def goals_command(update: Update, context):
    if not is_authorized(update.effective_chat.id):
        return
    goals = db_query("SELECT id, name, status FROM goals WHERE status = 'active'")
    lines = ["🎯 *Active Goals*\n"]
    for g in goals:
        projects = db_query("SELECT name, pipeline_stage FROM projects WHERE goal_id = ? AND status = 'active'", [g["id"]])
        lines.append(f"• *{g['name']}*")
        for p in projects:
            lines.append(f"    └ {p['name']} ({p['pipeline_stage']})")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def tasks_command(update: Update, context):
    if not is_authorized(update.effective_chat.id):
        return
    tasks = db_query("""
        SELECT t.id, t.title, t.status, t.assigned_to, t.pipeline_stage, p.name as project
        FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
        WHERE t.status NOT IN ('done', 'cancelled')
        ORDER BY t.priority ASC, t.created_at DESC LIMIT 15
    """)
    if not tasks:
        await update.message.reply_text("No pending tasks.")
        return
    lines = ["📋 *Pending Tasks*\n"]
    for t in tasks:
        icon = {"backlog": "⬜", "in_progress": "🔵", "review": "🟡", "blocked": "🔴"}.get(t["status"], "⬜")
        agent = t["assigned_to"] or "unassigned"
        lines.append(f"{icon} *{t['title']}*\n    {t['project']} | {t['pipeline_stage']} | {agent}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def rollup_command(update: Update, context):
    if not is_authorized(update.effective_chat.id):
        return
    await update.message.reply_text("☀️ Triggering morning rollup...")
    response = spawn_agent("apex")
    # Truncate if too long for Telegram
    if len(response) > 4000:
        response = response[:4000] + "\n\n... (truncated)"
    await update.message.reply_text(response)


async def spawn_command(update: Update, context):
    if not is_authorized(update.effective_chat.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /spawn <agent_name> [task_id]")
        return
    agent_name = args[0]
    task_id = args[1] if len(args) > 1 else None

    valid_agents = [r["agent_name"] for r in db_query("SELECT agent_name FROM agent_status")]
    if agent_name not in valid_agents:
        await update.message.reply_text(f"Unknown agent: {agent_name}\nAvailable: {', '.join(valid_agents)}")
        return

    await update.message.reply_text(f"🚀 Spawning {agent_name}...")
    response = spawn_agent(agent_name, task_id)
    if len(response) > 4000:
        response = response[:4000] + "\n\n... (truncated)"
    await update.message.reply_text(response)


async def handle_message(update: Update, context):
    """Route free-text messages through Apex."""
    if not is_authorized(update.effective_chat.id):
        return

    text = update.message.text
    if not text:
        return

    # Log the inbound message as a task for Apex to route
    task_id = f"msg-{int(datetime.now().timestamp())}"
    db_execute("""
        INSERT INTO agent_messages (from_agent, to_agent, msg_type, content, task_id, priority)
        VALUES ('abdul', 'apex', 'directive', ?, ?, 1)
    """, [text, task_id])

    await update.message.reply_text("📨 Routing to Apex...")

    # Spawn Apex to handle routing
    response = spawn_agent("apex")
    if len(response) > 4000:
        response = response[:4000] + "\n\n... (truncated)"
    await update.message.reply_text(response)


async def handle_callback(update: Update, context):
    """Handle inline button presses (Approve/Reject/Discuss etc.)."""
    query = update.callback_query
    await query.answer()
    data = query.data  # Format: "action:task_id" e.g. "approve:task-001"

    parts = data.split(":", 1)
    if len(parts) != 2:
        return

    action, task_id = parts

    if action == "approve":
        db_execute("UPDATE tasks SET status = 'done', completed_at = datetime('now') WHERE id = ?", [task_id])
        db_execute("UPDATE reviews SET verdict = 'approved', reviewed_at = datetime('now') WHERE task_id = ?", [task_id])
        await query.edit_message_text(f"✅ Approved: {task_id}")
    elif action == "reject":
        db_execute("UPDATE tasks SET status = 'backlog', review_status = 'rejected' WHERE id = ?", [task_id])
        db_execute("UPDATE reviews SET verdict = 'rejected', reviewed_at = datetime('now') WHERE task_id = ?", [task_id])
        await query.edit_message_text(f"❌ Rejected: {task_id}")
    elif action == "investigate":
        db_execute("""
            INSERT INTO agent_messages (from_agent, to_agent, msg_type, content, task_id, priority)
            VALUES ('abdul', 'analyst', 'directive', 'Investigate this signal further', ?, 1)
        """, [task_id])
        await query.edit_message_text(f"🔍 Investigating: {task_id} — routed to Analyst")


def send_to_abdul(text, buttons=None):
    """Outbound: Agent → Abdul. Call from spawn-agent.sh via CLI."""
    import requests
    if not TOKEN or not ALLOWED_CHAT_ID:
        print(f"[outbound] {text}")
        return

    payload = {"chat_id": ALLOWED_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    if buttons:
        keyboard = [[InlineKeyboardButton(b["text"], callback_data=b["data"]) for b in row] for row in buttons]
        payload["reply_markup"] = json.dumps({"inline_keyboard": keyboard})

    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json=payload)


def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("goals", goals_command))
    app.add_handler(CommandHandler("tasks", tasks_command))
    app.add_handler(CommandHandler("rollup", rollup_command))
    app.add_handler(CommandHandler("spawn", spawn_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("APEX Telegram bot running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
