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

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure repo root is on sys.path so `from kernel.api import ...` resolves
# regardless of working directory or PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from kernel.api import ApexKernel  # noqa: E402
from kernel.evidence import EvidenceStore  # noqa: E402
from adapters.publishers.linkedin import post_to_linkedin  # noqa: E402
from adapters.publishers.x_twitter import post_tweet, post_thread  # noqa: E402
from adapters.telegram.preferences import (  # noqa: E402
    UserPreferencesStore,
    DEFAULT_SOURCES,
    PLATFORM_INSTRUCTIONS,
    VALID_PLATFORMS,
)
from adapters.telegram.analytics import (  # noqa: E402
    generate_weekly_digest,
    format_digest_for_telegram,
    get_recent_published_posts,
)

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
evidence_store = EvidenceStore(str(APEX_HOME / "db" / "apex_state.db"))
prefs_store = UserPreferencesStore(str(APEX_HOME / "db" / "apex_state.db"))

# In-memory store for full writer drafts, keyed by writer_task_id.
# Allows the "Read Full Draft" button to retrieve the complete text.
_draft_store: dict[str, str] = {}

# Maps review_id → {workspace_id, writer_task_id, platform} so the
# approve_review callback can publish to X without re-querying the DB.
_approval_context_store: dict[int, dict[str, str]] = {}

_GLOBAL_CREDENTIAL_SCOPE = "global"


def _split_into_tweets(text: str, limit: int = 280) -> list[str]:
    """Split text into ≤limit-char chunks at word boundaries for a thread."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for word in text.split():
        if not current:
            current = word[:limit]
        elif len(current) + 1 + len(word) <= limit:
            current += " " + word
        else:
            chunks.append(current)
            current = word[:limit]
    if current:
        chunks.append(current)
    return chunks

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


def _split_message(text: str, limit: int = 4096) -> list[str]:
    """Split text into chunks ≤ limit chars, breaking at paragraph boundaries."""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        cut = remaining[:limit]
        # Prefer paragraph boundary
        boundary = cut.rfind("\n\n")
        if boundary < limit // 2:
            boundary = cut.rfind("\n")
        if boundary < limit // 4:
            boundary = limit
        chunks.append(remaining[:boundary].rstrip())
        remaining = remaining[boundary:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _update_task_description(task_id: str, description: str) -> None:
    """Overwrite the description of an existing task row directly in SQLite."""
    with kernel._connect() as conn:
        conn.execute(
            "UPDATE tasks SET description = ? WHERE id = ?",
            (description, task_id),
        )
        conn.commit()


async def _safe_reply(message: Any, text: str, **kwargs: Any) -> None:
    """Send a reply with Markdown; fall back to plain text if parsing fails."""
    try:
        await message.reply_text(text, **kwargs)
    except Exception:
        kwargs.pop("parse_mode", None)
        kwargs.pop("reply_markup", None)
        await message.reply_text(text, **kwargs)


def _icon_for_status(status: str) -> str:
    return {
        "idle": "🟢",
        "active": "🔵",
        "review": "🟡",
        "paused": "⏸️",
        "blocked": "🔴",
        "done": "✅",
    }.get(status, "⬜")


def _agent_role_icon(agent_name: str) -> str:
    role = agent_name.split("-")[-1].lower()
    return {
        "scout": "🔭",
        "writer": "✍️",
        "analyst": "📊",
        "builder": "🔨",
        "critic": "🛡️",
        "scheduler": "📅",
    }.get(role, "🤖")


# ── Task-chain helpers ───────────────────────────────────────────────────────

_MAX_CHAIN_HOPS: int = 3

# All role names that can be auto-chained (template names, not workspace-scoped)
_CHAIN_TARGETS: set[str] = {"analyst", "builder", "writer", "critic"}


def _critic_score(task_id: str) -> str:
    """Return formatted average eval score for a task, e.g. '4.2/5', or ''."""
    rows = kernel._fetch_all(
        "SELECT score, max_score FROM evals WHERE task_id = ? AND score IS NOT NULL",
        (task_id,),
    )
    if not rows:
        return ""
    scores = [r["score"] for r in rows if r["score"] is not None]
    if not scores:
        return ""
    return f"{sum(scores) / len(scores):.1f}/5"


def _dedupe_sources(task_id: str) -> list[dict[str, str]]:
    rows = _fetch_task_evidence(task_id)
    seen: set[str] = set()
    sources: list[dict[str, str]] = []
    for row in rows:
        results = row.get("results") or []
        if isinstance(results, str):
            try:
                results = json.loads(results)
            except Exception:
                results = []
        for result in results:
            url = str(result.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            sources.append({
                "title": str(result.get("title") or url).strip(),
                "url": url,
                "snippet": str(result.get("snippet") or "").strip(),
            })
    return sources


def _normalize_review_feedback(feedback: Any) -> dict[str, Any]:
    if isinstance(feedback, dict):
        return feedback
    if isinstance(feedback, str):
        try:
            parsed = json.loads(feedback)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {"summary": feedback}
    return {}


def _agent_exists(agent_id: str) -> bool:
    rows = kernel._fetch_all(
        "SELECT agent_name FROM agent_status WHERE agent_name = ?",
        (agent_id,),
    )
    return bool(rows)


def _smart_revision_route(
    review: dict[str, Any],
    workspace_id: str,
) -> tuple[str, str, str]:
    feedback_data = _normalize_review_feedback(review.get("feedback"))
    scores_raw = feedback_data.get("scores") or {}
    scores: dict[str, float] = {}
    if isinstance(scores_raw, dict):
        for key, value in scores_raw.items():
            if isinstance(value, (int, float)):
                scores[str(key).lower()] = float(value)

    original_agent = str(review.get("agent_name") or "").strip() or f"{workspace_id}-critic"
    scout_agent = f"{workspace_id}-scout"

    grounding_score = scores.get("grounding")
    accuracy_score = scores.get("accuracy")
    if (
        ((grounding_score is not None and grounding_score < 3)
         or (accuracy_score is not None and accuracy_score < 3))
        and _agent_exists(scout_agent)
    ):
        if grounding_score is not None and grounding_score < 3:
            reason = f"weak sources (grounding: {int(grounding_score)}/5)"
        else:
            assert accuracy_score is not None
            reason = f"weak sources (accuracy: {int(accuracy_score)}/5)"
        return scout_agent, "Scout", reason

    for dimension in ("completeness", "actionability", "conciseness"):
        score = scores.get(dimension)
        if score is not None and score < 3:
            label = original_agent.split("-")[-1].capitalize()
            return original_agent, label, f"incomplete output ({dimension}: {int(score)}/5)"

    label = original_agent.split("-")[-1].capitalize()
    return original_agent, label, "needs revision"


async def _handle_critic_chain(
    task_id: str,
    workspace_id: str,
    message: Any,
    emit_message: bool = True,
) -> dict[str, Any] | None:
    """Submit task for Critic review, run pipeline, post result for THIS task."""
    try:
        kernel.submit_for_review(task_id, "low")
    except ValueError:
        pass  # already queued — that's fine

    try:
        results = await asyncio.get_event_loop().run_in_executor(None, kernel.run_critic_pipeline)
    except Exception as exc:
        if emit_message:
            await _safe_reply(message, f"⚠️ Critic pipeline failed: {exc}")
        return None

    review = next((r for r in results if r.get("task_id") == task_id), None)
    if review is None:
        if emit_message:
            await _safe_reply(message, "⚠️ Critic found no review for this task.")
        return None

    verdict = (review.get("verdict") or "").upper()
    feedback_data = _normalize_review_feedback(review.get("feedback"))
    feedback = review.get("feedback") or ""
    if isinstance(feedback, dict):
        feedback = str(feedback.get("summary") or feedback)
    elif feedback_data:
        feedback = str(feedback_data.get("feedback") or feedback_data.get("summary") or feedback)

    score_str = _critic_score(task_id)
    score_part = f" ({score_str})" if score_str else ""

    if verdict == "PASS":
        text = f"✅ Critic approved{score_part}"
    elif verdict in ("REVISE", "BLOCK"):
        emoji = "🔄" if verdict == "REVISE" else "🚫"
        short = _summarise(str(feedback), 200)
        text = f"{emoji} Critic requested revision: {short}"
    else:
        text = f"🔍 Critic verdict: {verdict or 'pending'}{score_part}"

    if emit_message:
        await _safe_reply(message, text)

    if verdict in ("REVISE", "BLOCK"):
        route_agent, route_label, route_reason = _smart_revision_route(review, workspace_id)
        outbound_feedback = (
            feedback_data.get("feedback")
            or feedback_data.get("summary")
            or str(feedback)
        )
        try:
            kernel.send_message(
                "critic",
                route_agent,
                f"{verdict}: {outbound_feedback}",
                "review_feedback",
            )
        except Exception:
            pass
        if emit_message:
            await _safe_reply(
                message,
                f"🔄 Revision routed to {route_label} — {route_reason}",
            )
        review["revision_route"] = {
            "agent_id": route_agent,
            "label": route_label,
            "reason": route_reason,
        }

    review["score_str"] = score_str
    review["display_text"] = text
    return review


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


def _chain_summary(chain_log: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for step in chain_log:
        agent = str(step.get("agent", "agent")).split("-")[-1]
        label = agent.capitalize()
        icon = _agent_role_icon(agent)
        action = str(step.get("action") or step.get("status") or "completed").strip()
        lines.append(f"{icon} {label} → {action}")
    return "\n".join(lines)


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
    entries: list[str] = []
    for source in _dedupe_sources(task_id)[:5]:
        entries.append(f"• {source['title'][:55]}\n  {source['url']}")
    if not entries:
        return "📎 Sources: (no sources retrieved)"
    return "📎 Sources:\n" + "\n".join(entries)


def _infer_scout_action(result: dict[str, Any], task_id: str) -> str:
    text = " ".join(
        str(result.get(key) or "").strip()
        for key in ("actions_taken", "observations", "proposed_output")
    )
    text_lower = text.lower()
    topic_count: int | None = None
    import re
    match = re.search(r"(\d+)\s+(?:trending\s+)?(?:topics|ideas|signals|angles)", text_lower)
    if match:
        topic_count = int(match.group(1))
    elif result.get("proposed_output"):
        bullets = [
            line for line in str(result.get("proposed_output")).splitlines()
            if line.strip().startswith(("-", "*", "•")) or re.match(r"^\d+\.", line.strip())
        ]
        if bullets:
            topic_count = len(bullets)

    source_count = len(_dedupe_sources(task_id))
    if topic_count is not None and source_count:
        return f"Found {topic_count} trending topics ({source_count} sources)"
    if topic_count is not None:
        return f"Found {topic_count} trending topics"
    if source_count:
        return f"Found source-backed trends ({source_count} sources)"
    return "Scanned for timely content opportunities"


def _infer_writer_action(result: dict[str, Any]) -> str:
    text = " ".join(
        str(result.get(key) or "").strip()
        for key in ("actions_taken", "observations", "proposed_output")
    ).lower()
    if "linkedin" in text and "carousel" in text:
        return "Drafted LinkedIn carousel post"
    if "linkedin" in text and "post" in text:
        return "Drafted LinkedIn post"
    if "article" in text:
        return "Drafted article"
    if "caption" in text:
        return "Drafted caption set"
    if "thread" in text:
        return "Drafted thread"
    return "Drafted content"


def _infer_critic_action(review: dict[str, Any], task_id: str) -> str:
    verdict = str(review.get("verdict") or "").upper()
    score_str = review.get("score_str") or _critic_score(task_id)
    if verdict == "PASS":
        return f"Approved ({score_str})" if score_str else "Approved"
    if verdict == "REVISE":
        return f"Requested revision ({score_str})" if score_str else "Requested revision"
    if verdict == "BLOCK":
        return f"Blocked ({score_str})" if score_str else "Blocked"
    return verdict.title() if verdict else "Reviewed"


def _content_engine_operator_card(
    task_id: str,
    chain_log: list[dict[str, Any]],
    writer_preview: str,
    scout_task_id: str | None = None,
) -> str:
    preview = _summarise(writer_preview.strip(), 280) if writer_preview.strip() else "No draft preview available."
    # Evidence is stored under Scout's task_id; Writer task has no evidence rows.
    evidence_task_id = scout_task_id if scout_task_id else task_id
    sources = _dedupe_sources(evidence_task_id)
    # Fallback: also check writer task_id in case evidence was stored there
    if not sources and evidence_task_id != task_id:
        sources = _dedupe_sources(task_id)
    source_lines = [f"• {source['title']}" for source in sources[:3]]
    sources_block = "\n".join(source_lines) if source_lines else "• No verified sources"
    lines = [
        _chain_summary(chain_log),
        "",
        "📝 Draft Preview:",
        preview,
        "",
        f"📎 Sources: {len(sources)} verified",
        sources_block,
    ]
    return "\n".join(lines)


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
    template_id: str | None = None,
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

    role = agent_id.split("-")[-1].lower()
    if template_id == "content-engine":
        clean_summary = summary or "Completed step"
        lines = [f"{_agent_role_icon(role)} *{role.capitalize()}* → {clean_summary}"]
    else:
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
    "scout":   ["research", "find", "search", "analyze", "analyse", "investigate", "competitors", "trends", "trending"],
    "writer":  ["draft", "write", "create", "post", "article", "content"],
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
        "/connect linkedin <access_token> — Store LinkedIn publishing token\n"
        "/connect x <api_key> <api_secret> <access_token> <access_secret> — Store X credentials\n"
        "/evidence <task_id> — Stored evidence for a task\n"
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


async def evidence_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    args = context.args
    if not args:
        await update.message.reply_text("Usage: /evidence <task_id>")
        return

    task_id = args[0]
    try:
        evidence_rows = evidence_store.get_evidence(task_id)
    except Exception as exc:
        await update.message.reply_text(f"Failed to load evidence for `{task_id}`: {exc}", parse_mode="Markdown")
        return

    if not evidence_rows:
        await update.message.reply_text("No evidence stored for this task.")
        return

    chunks: list[str] = []
    for row in evidence_rows:
        query = row.get("query") or "unknown query"
        results = row.get("results") or []
        lines = [
            f"🔎 Query: {query}",
            f"Results: {len(results)}",
        ]
        for result in results:
            title = (result.get("title") or "Untitled").strip()
            url = (result.get("url") or "URL unavailable").strip()
            snippet = (result.get("snippet") or "").strip().replace("\n", " ")
            snippet = snippet[:100] + ("..." if len(snippet) > 100 else "")
            lines.append(f"- {title}")
            lines.append(f"  {url}")
            lines.append(f"  {snippet or 'No snippet'}")
        chunks.append("\n".join(lines))

    await _safe_reply(update.message, _truncate("\n\n".join(chunks), 4000))


async def _run_content_engine_chain(
    message: Any,
    loop: Any,
    workspace_id: str,
    scout_agent: str,
    scout_task_id: str,
    mission: str,
    goal_id: str | None,
    chain_log: list[dict[str, Any]],
) -> None:
    """
    Hardcoded Scout → Writer → Critic chain for content-engine workspaces.
    Scout runs on scout_task_id. Writer gets its own task (inherits evidence
    via workspace prefix). Critic runs via pipeline on the writer task.
    """
    # ── Load workspace preferences ──────────────────────────────────────────
    pref_sources = prefs_store.get_sources(workspace_id)
    pref_platform = prefs_store.get_platform(workspace_id)
    pref_voice_samples = prefs_store.get_voice_samples(workspace_id)

    # ── Step 1: Scout (inject source preferences into task description) ─────
    # List domain names only — no site: operators. DuckDuckGo's HTML search
    # cannot handle site: filters and returns 0 results when they appear in
    # queries. The query generator uses these domain names as topic bias only.
    source_hint = ", ".join(pref_sources)
    scout_desc = (
        f"Workspace: {workspace_id}\n"
        f"Issued via Telegram.\n\n"
        f"## Source Preferences\n"
        f"Prioritize content from these domains (do not use site: operators): {source_hint}"
    )
    try:
        _update_task_description(scout_task_id, scout_desc)
    except Exception as exc:
        print(f"[DEBUG] scout task desc update failed (non-fatal): {exc}", flush=True)

    print(f"[DEBUG] CE chain: spawning scout={scout_agent} task={scout_task_id}", flush=True)
    try:
        scout_resp = await loop.run_in_executor(None, kernel.spawn_agent, scout_agent, scout_task_id)
    except Exception as exc:
        print(f"[DEBUG] scout spawn failed: {exc}", flush=True)
        await _safe_reply(
            message,
            f"⚠️ Scout failed to start.\n"
            f"Error: {exc}\n\n"
            f"Use `/workspace {workspace_id}` to check agent status.",
            parse_mode="Markdown",
        )
        return

    scout_status = scout_resp.get("status", {})
    scout_state = scout_status.get("state", "") if isinstance(scout_status, dict) else str(scout_status)
    scout_evidence = _fetch_task_evidence(scout_task_id)
    scout_has_evidence = bool(scout_evidence)

    if scout_state == "blocked":
        if scout_has_evidence:
            # Evidence found despite blocked status — model mislabeled the status.
            # The search worked; continue the chain.
            print(
                f"[DEBUG] Scout returned blocked but {len(scout_evidence)} evidence row(s) found — treating as success",
                flush=True,
            )
        else:
            # Genuinely blocked with no evidence — abort with actionable message.
            await _safe_reply(
                message,
                "⚠️ Scout couldn't find enough sources for this topic.\n"
                "Try a broader topic, different keywords, or check that the Scout agent has web_search access.\n\n"
                f"Use `/workspace {workspace_id}` to check agent status or `/spawn` to retry manually.",
                parse_mode="Markdown",
            )
            return

    scout_action = _infer_scout_action(scout_resp, scout_task_id)
    print(f"[DEBUG] scout done: status={scout_state!r} evidence={len(scout_evidence)} action={scout_action!r}", flush=True)
    chain_log.append({"agent": scout_agent, "status": scout_resp.get("status"), "action": scout_action})
    await _safe_reply(message, f"🔍 Scout done — {scout_action}\n✍️ Spawning Writer…")

    # ── Step 2: Writer (inject platform + voice into task description) ──────
    writer_agent = f"{workspace_id}-writer"
    writer_desc_parts = [
        f"Workspace: {workspace_id}",
        f"Based on Scout research for: {scout_task_id}",
        "Use all available Search Evidence. Draft the content requested in the title.",
    ]
    if pref_platform and pref_platform in PLATFORM_INSTRUCTIONS:
        platform_instr = PLATFORM_INSTRUCTIONS[pref_platform]
        writer_desc_parts.append(
            f"\n## Platform Instructions ({pref_platform.upper()})\n{platform_instr}"
        )
    if pref_voice_samples:
        samples_block = "\n\n---\n\n".join(pref_voice_samples)
        writer_desc_parts.append(
            f"\n## Voice Reference\n"
            f"Match the tone, style, and structure of these sample posts:\n\n{samples_block}"
        )
    try:
        writer_task_id = kernel.create_task({
            "goal_id": goal_id,
            "title": mission,
            "description": "\n".join(writer_desc_parts),
            "status": "backlog",
        })
        kernel.assign_task(writer_task_id, writer_agent)
    except Exception as exc:
        print(f"[DEBUG] writer task creation failed: {exc}", flush=True)
        await _safe_reply(message, f"⚠️ Writer task creation failed: {exc}")
        return

    print(f"[DEBUG] CE chain: spawning writer={writer_agent} task={writer_task_id}", flush=True)
    try:
        writer_resp = await loop.run_in_executor(None, kernel.spawn_agent, writer_agent, writer_task_id)
    except Exception as exc:
        print(f"[DEBUG] writer spawn failed: {exc}", flush=True)
        await _safe_reply(
            message,
            f"⚠️ Writer failed to generate a draft.\n"
            f"Error: {exc}\n\n"
            f"Scout's research is saved. Use `/spawn {writer_agent}` to retry.",
            parse_mode="Markdown",
        )
        return

    writer_preview = str(writer_resp.get("proposed_output") or "")
    writer_action = _infer_writer_action(writer_resp)
    writer_status = writer_resp.get("status", {})
    writer_state = writer_status.get("state", "") if isinstance(writer_status, dict) else str(writer_status)
    print(f"[DEBUG] writer done: status={writer_state} preview={writer_preview[:60]!r}", flush=True)
    chain_log.append({"agent": writer_agent, "status": writer_resp.get("status"), "action": writer_action})

    if not writer_preview:
        print("[DEBUG] writer returned empty proposed_output", flush=True)
        await _safe_reply(
            message,
            "⚠️ Writer ran but produced no draft output.\n"
            "This usually means the model hit its context limit. "
            "Trying to continue to Critic anyway…",
        )

    # ── Step 2b: Humanize pass ─────────────────────────────────────────────
    # A second Writer task rewrites the draft to strip AI-generated patterns.
    # The humanized output replaces the original draft for Critic review.
    humanize_succeeded = False
    if writer_preview:
        await _safe_reply(message, f"✍️ Writer done — {writer_action}\n🪄 Humanizing draft…")
        humanize_desc = (
            f"Workspace: {workspace_id}\n"
            "HUMANIZE PASS: Rewrite the draft below to sound authentically human.\n\n"
            "Rules:\n"
            "- Remove all corporate language, buzzwords, and filler phrases\n"
            "- Replace any bullet points or numbered lists with flowing paragraphs\n"
            "- Add one specific personal observation that only someone who has done this would know\n"
            "- Use a conversational tone — short sentences, real words, no jargon\n"
            "- The reader should think 'this person has actually done this', not 'this person researched this'\n"
            "- If another AI could detect this as AI-generated, it is not good enough\n\n"
            f"Original draft:\n{writer_preview}"
        )
        if pref_platform and pref_platform in PLATFORM_INSTRUCTIONS:
            humanize_desc += f"\n\n## Platform Instructions ({pref_platform.upper()})\n{PLATFORM_INSTRUCTIONS[pref_platform]}"
        try:
            humanize_task_id = kernel.create_task({
                "goal_id": goal_id,
                "title": f"[Humanize] {mission}",
                "description": humanize_desc,
                "status": "backlog",
            })
            kernel.assign_task(humanize_task_id, writer_agent)
            print(f"[DEBUG] humanize task={humanize_task_id}", flush=True)
            humanize_resp = await loop.run_in_executor(None, kernel.spawn_agent, writer_agent, humanize_task_id)
            humanized = str(humanize_resp.get("proposed_output") or "").strip()
            if humanized:
                writer_preview = humanized
                writer_task_id = humanize_task_id
                humanize_succeeded = True
                chain_log.append({
                    "agent": writer_agent,
                    "status": humanize_resp.get("status"),
                    "action": "Humanized draft",
                })
                print(f"[DEBUG] humanize done: preview={writer_preview[:60]!r}", flush=True)
            else:
                print("[DEBUG] humanize returned empty — keeping original draft", flush=True)
        except Exception as exc:
            print(f"[DEBUG] humanize pass failed (non-fatal): {exc}", flush=True)
    else:
        await _safe_reply(message, f"✍️ Writer done — {writer_action}\n🔍 Running Critic…")

    # Store full draft for "Read Full Draft" button retrieval
    if writer_preview:
        _draft_store[writer_task_id] = writer_preview

    # ── Step 3: Critic (+ 1 revision loop if REVISE) ──────────────────────
    critic_intro = "🪄 Humanized\n🔍 Running Critic…" if humanize_succeeded else "🔍 Running Critic…"
    await _safe_reply(message, critic_intro)
    print(f"[DEBUG] critic start: writer_task={writer_task_id}", flush=True)
    try:
        review = await _handle_critic_chain(writer_task_id, workspace_id, message, emit_message=False)
    except Exception as exc:
        print(f"[DEBUG] critic exception: {exc}", flush=True)
        chain_summary = _chain_summary(chain_log)
        draft_section = f"\n\n📝 Draft:\n{_summarise(writer_preview, 300)}" if writer_preview else ""
        await _safe_reply(
            message,
            f"⚠️ Critic pipeline failed: {exc}\n\n"
            f"{chain_summary}{draft_section}\n\n"
            f"Chain failed at Critic. Use `/workspace {workspace_id}` to check status.",
            parse_mode="Markdown",
        )
        return
    print(f"[DEBUG] critic done: review={'None' if review is None else review.get('verdict')}", flush=True)

    # ── Step 3b: Revision loop (capped at 1 pass) ─────────────────────────
    if review is not None and (review.get("verdict") or "").upper() == "REVISE":
        chain_log.append({
            "agent": f"{workspace_id}-critic",
            "status": "REVISE",
            "action": _infer_critic_action(review, writer_task_id),
        })
        # Extract feedback to inject into revision prompt
        feedback_data = _normalize_review_feedback(review.get("feedback"))
        critic_feedback = (
            feedback_data.get("feedback")
            or feedback_data.get("summary")
            or str(review.get("feedback") or "")
        )
        await _safe_reply(
            message,
            f"🔄 Critic requested revision → Writer revising → Critic re-reviewing\n"
            f"_{_summarise(critic_feedback, 120)}_",
            parse_mode="Markdown",
        )

        # Create a new Writer task with Critic feedback + original prefs injected
        revision_desc_parts = [
            f"Workspace: {workspace_id}",
            f"REVISION REQUEST from Critic: {critic_feedback}",
            "",
            f"Based on Scout research for: {scout_task_id}",
            f"Previous draft task: {writer_task_id}",
            "Address all Critic feedback. Use all available Search Evidence.",
        ]
        if pref_platform and pref_platform in PLATFORM_INSTRUCTIONS:
            revision_desc_parts.append(
                f"\n## Platform Instructions ({pref_platform.upper()})\n"
                f"{PLATFORM_INSTRUCTIONS[pref_platform]}"
            )
        if pref_voice_samples:
            samples_block = "\n\n---\n\n".join(pref_voice_samples)
            revision_desc_parts.append(
                f"\n## Voice Reference\n"
                f"Match the tone, style, and structure of these sample posts:\n\n{samples_block}"
            )
        try:
            revision_task_id = kernel.create_task({
                "goal_id": goal_id,
                "title": mission,
                "description": "\n".join(revision_desc_parts),
                "status": "backlog",
            })
            kernel.assign_task(revision_task_id, writer_agent)
        except Exception as exc:
            print(f"[DEBUG] revision task creation failed: {exc}", flush=True)
            await _safe_reply(message, f"⚠️ Revision task creation failed: {exc}")
            # Fall through with original review
        else:
            try:
                rev_resp = await loop.run_in_executor(None, kernel.spawn_agent, writer_agent, revision_task_id)
            except Exception as exc:
                print(f"[DEBUG] revision writer spawn failed: {exc}", flush=True)
                await _safe_reply(message, f"⚠️ Revision writer failed: {exc}")
            else:
                writer_preview = str(rev_resp.get("proposed_output") or writer_preview)
                writer_task_id = revision_task_id  # Critic will review the revised task
                rev_action = _infer_writer_action(rev_resp)
                print(f"[DEBUG] revision writer done: preview={writer_preview[:60]!r}", flush=True)
                chain_log.append({"agent": writer_agent, "status": rev_resp.get("status"), "action": f"Revised — {rev_action}"})
                # Update draft store with revised output
                if writer_preview:
                    _draft_store[writer_task_id] = writer_preview

                # Re-run Critic on the revised task
                try:
                    review = await _handle_critic_chain(writer_task_id, workspace_id, message, emit_message=False)
                    print(f"[DEBUG] re-critic done: verdict={review.get('verdict') if review else 'None'}", flush=True)
                    if review is not None:
                        chain_log.append({
                            "agent": f"{workspace_id}-critic",
                            "status": review.get("verdict"),
                            "action": _infer_critic_action(review, writer_task_id),
                        })
                except Exception as exc:
                    print(f"[DEBUG] re-critic failed: {exc}", flush=True)
                    review = None

    if review is not None:
        # Only append Critic to chain_log if not already appended above
        if not any(
            step.get("agent") == f"{workspace_id}-critic"
            for step in chain_log
        ):
            chain_log.append({
                "agent": f"{workspace_id}-critic",
                "status": review.get("verdict"),
                "action": _infer_critic_action(review, writer_task_id),
            })
        review_id = review.get("id")
        reply_markup = None
        keyboard_rows = []
        if review_id is not None:
            # Store context so approve_review callback can publish to X
            _approval_context_store[review_id] = {
                "workspace_id": workspace_id,
                "writer_task_id": writer_task_id,
                "platform": pref_platform or "",
            }
            keyboard_rows.append([
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_review:{review_id}"),
                InlineKeyboardButton("✏️ Edit", callback_data=f"edit_review:{review_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_review:{review_id}"),
            ])
        # Always add "Read Full Draft" when we have a full draft stored
        if writer_task_id in _draft_store:
            keyboard_rows.append([
                InlineKeyboardButton("📄 Read Full Draft", callback_data=f"read_draft:{writer_task_id}"),
            ])
        if keyboard_rows:
            reply_markup = InlineKeyboardMarkup(keyboard_rows)
        try:
            card = _content_engine_operator_card(writer_task_id, chain_log, writer_preview, scout_task_id=scout_task_id)
            print(f"[DEBUG] operator card ({len(card)} chars): {card[:100]!r}", flush=True)
            await message.reply_text(card, reply_markup=reply_markup)
        except Exception as exc:
            print(f"[DEBUG] operator card failed: {exc}", flush=True)
            fallback = _chain_summary(chain_log)
            if writer_preview:
                fallback += f"\n\n📝 Draft:\n{_summarise(writer_preview, 300)}"
            await _safe_reply(message, fallback)
    else:
        # Critic pipeline returned nothing — send draft + chain state so user isn't left hanging
        print(f"[DEBUG] critic returned None, sending chain summary + draft", flush=True)
        chain_summary = _chain_summary(chain_log)
        draft_section = f"\n\n📝 Draft:\n{_summarise(writer_preview, 300)}" if writer_preview else ""
        no_review_note = (
            "\n\n⚠️ Critic produced no review (queue may have been empty or review already processed). "
            f"Use `/approvals` to check the queue or `/workspace {workspace_id}` for status."
        )
        await _safe_reply(
            message,
            f"{chain_summary}{draft_section}{no_review_note}",
            parse_mode="Markdown",
        )


async def _run_generic_chain(
    message: Any,
    loop: Any,
    workspace_id: str,
    agent_id: str,
    task_id: str,
    template_id: str | None,
    chain_log: list[dict[str, Any]],
) -> None:
    """Message-based chaining for non-content-engine templates."""
    current_agent = agent_id
    for hop in range(1, _MAX_CHAIN_HOPS + 1):
        print(f"[DEBUG] spawn start: agent={current_agent} task={task_id} hop={hop}", flush=True)
        try:
            response = await loop.run_in_executor(None, kernel.spawn_agent, current_agent, task_id)
        except Exception as exc:
            print(f"[DEBUG] spawn exception: {exc}", flush=True)
            await message.reply_text(f"Agent spawn failed ({current_agent}): {exc}")
            break
        print(f"[DEBUG] spawn done: agent={current_agent} status={response.get('status')}", flush=True)

        status_obj = response.get("status", {})
        action_summary = (
            (response.get("actions_taken") or "").strip()
            or (response.get("observations") or "").strip()
            or (response.get("proposed_output") or "").strip()
        )
        if not action_summary:
            if isinstance(status_obj, dict):
                action_summary = f"{status_obj.get('state', 'completed')} {status_obj.get('reason') or status_obj.get('stakes') or ''}".strip()
            else:
                action_summary = str(status_obj)
        chain_log.append({
            "agent": current_agent,
            "status": response.get("status"),
            "action": _summarise(action_summary, 60),
        })

        try:
            card = _format_task_card(current_agent, response, task_id, workspace_id, template_id=template_id)
            print(f"[DEBUG] sending card ({len(card)} chars): {card[:80]!r}", flush=True)
            await _safe_reply(message, card, parse_mode="Markdown")
        except Exception as exc:
            print(f"[DEBUG] card send failed: {exc}", flush=True)
            await _safe_reply(message, f"Agent {current_agent} done — status: {status_obj}")

        if hop < _MAX_CHAIN_HOPS:
            next_agent = _next_chain_agent(workspace_id, response.get("messages", []))
            if next_agent:
                next_role = next_agent.rsplit("-", 1)[-1]
                if next_role == "critic":
                    await _safe_reply(message, "🔍 Running Critic review…")
                    review = await _handle_critic_chain(task_id, workspace_id, message, emit_message=True)
                    if review is not None:
                        chain_log.append({
                            "agent": f"{workspace_id}-critic",
                            "status": review.get("verdict"),
                            "action": _infer_critic_action(review, task_id),
                        })
                    break
                await _safe_reply(message, f"🔗 Chaining to `{next_agent}`…", parse_mode="Markdown")
                current_agent = next_agent
                continue
        break

    if chain_log:
        await _safe_reply(message, "🔗 Chain Summary\n" + _chain_summary(chain_log))


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
    template_id = ws.get("template_id")
    is_content_engine = template_id == "content-engine"

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

    loop = asyncio.get_event_loop()
    chain_log: list[dict[str, Any]] = []

    if is_content_engine:
        await _run_content_engine_chain(
            update.message, loop, workspace_id, agent_id, task_id, mission, goal_id, chain_log
        )
    else:
        await _run_generic_chain(
            update.message, loop, workspace_id, agent_id, task_id, template_id, chain_log
        )


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
    response = await asyncio.get_event_loop().run_in_executor(None, kernel.spawn_agent, "apex")
    await _safe_reply(update.message, _truncate(_format_spawn_result(response)))


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
    response = await asyncio.get_event_loop().run_in_executor(
        None, kernel.spawn_agent, agent_name, task_id
    )
    await _safe_reply(update.message, _truncate(_format_spawn_result(response)))


async def preferences_command(update: Update, context: Any) -> None:
    """
    /preferences <workspace_id> [add <domain> | remove <domain> | reset]

    Manage preferred source domains for Scout's search queries.
    Default sources are used when none are stored.
    """
    if not is_authorized(update.effective_chat.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "  /preferences <ws> — show current sources\n"
            "  /preferences <ws> add <domain> [domain2 ...]\n"
            "  /preferences <ws> remove <domain> [domain2 ...]\n"
            "  /preferences <ws> reset"
        )
        return

    workspace_id = args[0]
    try:
        ws = kernel.get_workspace(workspace_id)
        if ws.get("status") == "deleted":
            raise ValueError("deleted")
    except ValueError:
        await update.message.reply_text(f"Workspace `{workspace_id}` not found.", parse_mode="Markdown")
        return

    sub = args[1].lower() if len(args) > 1 else "show"

    if sub == "add":
        if len(args) < 3:
            await update.message.reply_text("Usage: /preferences <ws> add <domain> [domain2 ...]")
            return
        domains = [a.lower().strip() for a in args[2:]]
        for domain in domains:
            prefs_store.add_source(workspace_id, domain)
        sources = prefs_store.get_sources(workspace_id)
        added_list = ", ".join(f"`{d}`" for d in domains)
        await update.message.reply_text(
            f"✅ Added {added_list}\n\nCurrent sources:\n" + "\n".join(f"• {s}" for s in sources),
            parse_mode="Markdown",
        )

    elif sub == "remove":
        if len(args) < 3:
            await update.message.reply_text("Usage: /preferences <ws> remove <domain> [domain2 ...]")
            return
        domains = [a.lower().strip() for a in args[2:]]
        removed_domains = [d for d in domains if prefs_store.remove_source(workspace_id, d)]
        not_found = [d for d in domains if d not in removed_domains]
        sources = prefs_store.get_sources(workspace_id)
        lines = []
        if removed_domains:
            removed_list = ", ".join(f"`{d}`" for d in removed_domains)
            lines.append(f"🗑️ Removed {removed_list}")
        if not_found:
            nf_list = ", ".join(f"`{d}`" for d in not_found)
            lines.append(f"⚠️ Not found: {nf_list}")
        lines += ["", "Current sources:"] + [f"• {s}" for s in sources]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    elif sub == "reset":
        prefs_store.reset_sources(workspace_id)
        await update.message.reply_text(
            "🔄 Reset to defaults:\n" + "\n".join(f"• {s}" for s in DEFAULT_SOURCES)
        )

    else:
        # Show current sources
        sources = prefs_store.get_sources(workspace_id)
        platform = prefs_store.get_platform(workspace_id)
        voice_count = len(prefs_store.get_voice_samples(workspace_id))
        is_default = sources == list(DEFAULT_SOURCES)
        source_label = "_(defaults)_" if is_default else ""
        lines = [
            f"⚙️ *Preferences for* `{workspace_id}`",
            "",
            f"📎 *Sources* {source_label}",
        ] + [f"  • {s}" for s in sources] + [
            "",
            f"🎯 *Platform:* {platform or '_(not set)_'}",
            f"🗣️ *Voice samples:* {voice_count}/10",
            "",
            "Commands:",
            "  `/preferences <ws> add <domain> [domain2 ...]`",
            "  `/preferences <ws> remove <domain> [domain2 ...]`",
            "  `/preferences <ws> reset`",
            "  `/platform <ws> <linkedin|x|tiktok|instagram>`",
            "  `/voice <ws> <post text>`",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def voice_command(update: Update, context: Any) -> None:
    """
    /voice <workspace_id> [clear]
    /voice <workspace_id> <post text>

    Manage voice samples used to guide Writer tone and style.
    Max 10 samples. Each call to /voice <ws> <text> adds one sample.
    /voice <ws> clear — removes all samples.
    """
    if not is_authorized(update.effective_chat.id):
        return

    # Parse raw text to preserve newlines in multi-line posts
    raw = update.message.text or ""
    raw_parts = raw.split(None, 2)  # ["/voice", "<ws>", "<post text with newlines>"]

    if len(raw_parts) < 2:
        await update.message.reply_text(
            "Usage:\n"
            "  /voice <ws> <post text> — add a voice sample\n"
            "  /voice <ws> clear — remove all samples\n"
            "  /voice <ws> — show current samples"
        )
        return

    workspace_id = raw_parts[1]
    try:
        ws = kernel.get_workspace(workspace_id)
        if ws.get("status") == "deleted":
            raise ValueError("deleted")
    except ValueError:
        await update.message.reply_text(f"Workspace `{workspace_id}` not found.", parse_mode="Markdown")
        return

    post_text = raw_parts[2].strip() if len(raw_parts) > 2 else ""

    if post_text.lower() == "clear":
        n = prefs_store.clear_voice_samples(workspace_id)
        await update.message.reply_text(f"🗑️ Cleared {n} voice sample(s).")
        return

    if not post_text:
        # Show current samples
        samples = prefs_store.get_voice_samples(workspace_id)
        if not samples:
            await update.message.reply_text(
                f"No voice samples stored for `{workspace_id}`.\n\n"
                "Add one with:\n`/voice <ws> <your best post text>`",
                parse_mode="Markdown",
            )
            return
        lines = [f"🗣️ *Voice samples for* `{workspace_id}` ({len(samples)}/10)\n"]
        for i, s in enumerate(samples, 1):
            lines.append(f"*{i}.* {_summarise(s, 120)}")
        lines.append("\n`/voice <ws> clear` to reset all samples.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # Add the sample
    try:
        count = prefs_store.add_voice_sample(workspace_id, post_text)
        await update.message.reply_text(
            f"✅ Voice sample {count}/10 saved.\n\n"
            f"_Preview: {_summarise(post_text, 100)}_\n\n"
            "Writer will match this tone on next task.",
            parse_mode="Markdown",
        )
    except ValueError as exc:
        await update.message.reply_text(f"⚠️ {exc}")


async def platform_command(update: Update, context: Any) -> None:
    """
    /platform <workspace_id> <linkedin|x|tiktok|instagram>
    /platform <workspace_id> — show current platform

    Sets the target platform. Writer receives platform-specific formatting
    instructions on every task in this workspace.
    """
    if not is_authorized(update.effective_chat.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: /platform <workspace_id> <linkedin|x|tiktok|instagram>"
        )
        return

    workspace_id = args[0]
    try:
        ws = kernel.get_workspace(workspace_id)
        if ws.get("status") == "deleted":
            raise ValueError("deleted")
    except ValueError:
        await update.message.reply_text(f"Workspace `{workspace_id}` not found.", parse_mode="Markdown")
        return

    if len(args) < 2:
        platform = prefs_store.get_platform(workspace_id)
        if platform:
            instr = PLATFORM_INSTRUCTIONS.get(platform, "")
            await update.message.reply_text(
                f"🎯 *Platform:* `{platform}`\n\n_{instr}_",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"No platform set for `{workspace_id}`.\n\n"
                f"Options: {', '.join(VALID_PLATFORMS)}\n"
                "Usage: `/platform <ws> <platform>`",
                parse_mode="Markdown",
            )
        return

    platform = args[1].lower().strip()
    if platform not in VALID_PLATFORMS:
        await update.message.reply_text(
            f"Unknown platform `{platform}`.\n"
            f"Valid options: {', '.join(sorted(VALID_PLATFORMS))}",
            parse_mode="Markdown",
        )
        return

    prefs_store.set_platform(workspace_id, platform)
    instr = PLATFORM_INSTRUCTIONS[platform]
    await update.message.reply_text(
        f"✅ Platform set to *{platform}*\n\n_{instr}_",
        parse_mode="Markdown",
    )


async def xcreds_command(update: Update, context: Any) -> None:
    """
    /xcreds <workspace_id> <api_key> <api_secret> <access_token> <access_secret>
    /xcreds <workspace_id> clear
    /xcreds <workspace_id> — show status (does not reveal secrets)

    Stores X (Twitter) OAuth 1.0a credentials for auto-publishing approved drafts.
    Get credentials at developer.x.com — App settings → Keys and Tokens.
    """
    if not is_authorized(update.effective_chat.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "  `/xcreds <ws_id> <api_key> <api_secret> <access_token> <access_secret>`\n"
            "  `/xcreds <ws_id> clear` — remove stored credentials\n"
            "  `/xcreds <ws_id>` — show connection status",
            parse_mode="Markdown",
        )
        return

    workspace_id = args[0]

    if len(args) == 2 and args[1].lower() == "clear":
        prefs_store.clear_x_credentials(workspace_id)
        await update.message.reply_text(f"🗑 X credentials cleared for `{workspace_id}`.", parse_mode="Markdown")
        return

    if len(args) == 1:
        creds = prefs_store.get_x_credentials(workspace_id)
        if creds:
            await update.message.reply_text(
                f"✅ X credentials configured for `{workspace_id}`.\n"
                f"API Key: `{creds['api_key'][:8]}...`",
                parse_mode="Markdown",
            )
        else:
            await update.message.reply_text(
                f"❌ No X credentials found for `{workspace_id}`.\n\n"
                "Use:\n"
                "`/xcreds <ws_id> <api_key> <api_secret> <access_token> <access_secret>`",
                parse_mode="Markdown",
            )
        return

    if len(args) != 5:
        await update.message.reply_text(
            "Expected 5 arguments: `<ws_id> <api_key> <api_secret> <access_token> <access_secret>`",
            parse_mode="Markdown",
        )
        return

    _, api_key, api_secret, access_token, access_secret = args
    prefs_store.set_x_credentials(workspace_id, api_key, api_secret, access_token, access_secret)
    await update.message.reply_text(
        f"✅ X credentials saved for `{workspace_id}`.\n"
        "Approved drafts with platform=x will now auto-publish.",
        parse_mode="Markdown",
    )


async def connect_command(update: Update, context: Any) -> None:
    if not is_authorized(update.effective_chat.id):
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage:\n"
            "  /connect linkedin <access_token>\n"
            "  /connect x <api_key> <api_secret> <access_token> <access_secret>"
        )
        return

    provider = args[0].lower().strip()
    if provider == "linkedin":
        if len(args) != 2:
            await update.message.reply_text("Usage: /connect linkedin <access_token>")
            return
        prefs_store.set_pref(_GLOBAL_CREDENTIAL_SCOPE, "credential", "linkedin_access_token", args[1].strip())
        await update.message.reply_text("✅ LinkedIn credential stored for auto-publishing.")
        return

    if provider == "x":
        if len(args) != 5:
            await update.message.reply_text(
                "Usage: /connect x <api_key> <api_secret> <access_token> <access_secret>"
            )
            return
        prefs_store.set_pref(_GLOBAL_CREDENTIAL_SCOPE, "credential", "x_api_key", args[1].strip())
        prefs_store.set_pref(_GLOBAL_CREDENTIAL_SCOPE, "credential", "x_api_secret", args[2].strip())
        prefs_store.set_pref(_GLOBAL_CREDENTIAL_SCOPE, "credential", "x_access_token", args[3].strip())
        prefs_store.set_pref(_GLOBAL_CREDENTIAL_SCOPE, "credential", "x_access_secret", args[4].strip())
        await update.message.reply_text("✅ X credentials stored.")
        return

    await update.message.reply_text("Supported providers: linkedin, x")


async def handle_message(update: Update, context: Any) -> None:
    """Route free-text messages through Apex."""
    if not is_authorized(update.effective_chat.id):
        return

    text = update.message.text
    if not text:
        return

    await update.message.reply_text("📨 Routing to Apex...")
    response = await asyncio.get_event_loop().run_in_executor(None, kernel.route_user_message, text)
    await _safe_reply(update.message, _truncate(_format_spawn_result(response)))


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
            review_rows = kernel._fetch_all(
                """
                SELECT r.id, r.task_id, t.workspace_id
                FROM reviews r
                LEFT JOIN tasks t ON t.id = r.task_id
                WHERE r.id = ?
                """,
                (int(value),),
            )
            workspace_id = review_rows[0]["workspace_id"] if review_rows else None
            task_id = review_rows[0]["task_id"] if review_rows else None

            kernel.approve_action(int(value))

            auto_published = False
            publish_msg = ""
            if workspace_id and task_id:
                try:
                    ws = kernel.get_workspace(workspace_id)
                except Exception:
                    ws = {}
                platform = prefs_store.get_platform(workspace_id)
                # Use _approval_context_store for writer_task_id when available
                ctx = _approval_context_store.get(int(value), {})
                writer_task_id = ctx.get("writer_task_id") or task_id
                full_draft = _draft_store.get(writer_task_id) or _draft_store.get(task_id)
                access_token = prefs_store.get_pref(
                    _GLOBAL_CREDENTIAL_SCOPE,
                    "credential",
                    "linkedin_access_token",
                )
                if (
                    ws.get("template_id") == "content-engine"
                    and platform == "linkedin"
                    and access_token
                    and full_draft
                ):
                    published = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: post_to_linkedin(str(access_token), full_draft),
                    )
                    auto_published = True
                    publish_msg = f"✅ Published to LinkedIn! Post URL: {published.get('post_url', '')}"
                elif ws.get("template_id") == "content-engine" and platform == "linkedin":
                    publish_msg = "✅ Approved. Connect LinkedIn to auto-publish: /connect linkedin <access_token>"
                elif (
                    ws.get("template_id") == "content-engine"
                    and platform == "x"
                    and full_draft
                ):
                    x_creds = prefs_store.get_x_credentials(workspace_id)
                    if x_creds:
                        try:
                            if len(full_draft) <= 280:
                                result = await asyncio.get_event_loop().run_in_executor(
                                    None,
                                    lambda: post_tweet(
                                        api_key=x_creds["api_key"],
                                        api_secret=x_creds["api_secret"],
                                        access_token=x_creds["access_token"],
                                        access_secret=x_creds["access_secret"],
                                        text=full_draft,
                                    ),
                                )
                                auto_published = True
                                publish_msg = f"✅ Published to X! Tweet URL: {result['tweet_url']}"
                            else:
                                tweets = _split_into_tweets(full_draft)
                                tweet_ids = await asyncio.get_event_loop().run_in_executor(
                                    None,
                                    lambda: post_thread(
                                        api_key=x_creds["api_key"],
                                        api_secret=x_creds["api_secret"],
                                        access_token=x_creds["access_token"],
                                        access_secret=x_creds["access_secret"],
                                        tweets=tweets,
                                    ),
                                )
                                auto_published = True
                                url = f"https://x.com/i/web/status/{tweet_ids[0]}"
                                publish_msg = f"✅ Published to X as {len(tweets)}-tweet thread! URL: {url}"
                        except Exception as x_exc:
                            publish_msg = f"⚠️ X publishing failed: {x_exc}"
                    else:
                        publish_msg = (
                            "ℹ️ Platform is X but no credentials found for this workspace.\n"
                            "Use `/xcreds <ws_id> <api_key> <api_secret> <access_token> <access_secret>` to connect X."
                        )

            original_text = query.message.text or ""
            approved_text = original_text + "\n\n✅ Approved."
            await query.edit_message_text(approved_text)
            if publish_msg:
                await query.message.reply_text(publish_msg)
            elif not auto_published:
                await query.message.reply_text("✅ Approved.")
        except Exception as exc:
            await query.message.reply_text(f"Approval failed: {exc}")
        return

    if action == "reject_review":
        try:
            kernel.reject_action(int(value), "Rejected via Telegram")
            original_text = query.message.text or ""
            rejected_text = original_text + "\n\n❌ Rejected. Draft discarded."
            await query.edit_message_text(rejected_text)
        except Exception as exc:
            await query.message.reply_text(f"Rejection failed: {exc}")
        return

    if action == "edit_review":
        try:
            kernel.reject_action(int(value), "Edit requested via Telegram")
            original_text = query.message.text or ""
            edit_text = original_text + "\n\n✏️ Sent back for revision."
            await query.edit_message_text(edit_text)
        except Exception as exc:
            await query.message.reply_text(f"Edit request failed: {exc}")
        return

    if action == "read_draft":
        full_text = _draft_store.get(value)
        if not full_text:
            await query.message.reply_text("Draft not available (session may have restarted).")
            return
        chunks = _split_message(full_text, limit=4096)
        for i, chunk in enumerate(chunks):
            header = f"📄 *Full Draft* ({i + 1}/{len(chunks)})\n\n" if len(chunks) > 1 else "📄 *Full Draft*\n\n"
            await query.message.reply_text(header + chunk)
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


async def digest_command(update: Update, context: Any) -> None:
    """
    /digest <workspace_id>

    Generate and send the weekly content digest for a workspace:
    total posts, average engagement, best post, and per-platform breakdowns.
    """
    if not is_authorized(update.effective_chat.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /digest <workspace_id>")
        return

    workspace_id = args[0]
    try:
        ws = kernel.get_workspace(workspace_id)
        if ws.get("status") == "deleted":
            raise ValueError("deleted")
    except ValueError:
        await update.message.reply_text(f"Workspace `{workspace_id}` not found.", parse_mode="Markdown")
        return

    try:
        digest = generate_weekly_digest(workspace_id)
    except Exception as exc:
        await update.message.reply_text(f"Failed to generate digest: {exc}")
        return

    if digest.get("total_posts", 0) == 0 and digest.get("records_considered", 0) == 0:
        await update.message.reply_text("No posts published yet this week.")
        return

    await update.message.reply_text(format_digest_for_telegram(digest))


async def published_command(update: Update, context: Any) -> None:
    """
    /published <workspace_id>

    Show the last 5 published posts for a workspace with their engagement
    metrics (likes, comments, reposts, impressions) if recorded.
    """
    if not is_authorized(update.effective_chat.id):
        return
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /published <workspace_id>")
        return

    workspace_id = args[0]
    try:
        ws = kernel.get_workspace(workspace_id)
        if ws.get("status") == "deleted":
            raise ValueError("deleted")
    except ValueError:
        await update.message.reply_text(f"Workspace `{workspace_id}` not found.", parse_mode="Markdown")
        return

    try:
        posts = get_recent_published_posts(workspace_id, limit=5)
    except Exception as exc:
        await update.message.reply_text(f"Failed to load published posts: {exc}")
        return

    if not posts:
        await update.message.reply_text("No posts published yet this week.")
        return

    lines = [f"📰 *Last {len(posts)} published posts* — `{workspace_id}`", ""]
    for i, post in enumerate(posts, 1):
        platform = post["platform"] or "unknown"
        recorded_at = post["recorded_at"][:10] if post["recorded_at"] else "unknown date"
        task_id = post["task_id"]

        lines.append(f"*{i}. {platform}* — {recorded_at}")
        lines.append(f"   Task: `{task_id}`")

        has_engagement = any(post[k] > 0 for k in ("likes", "comments", "reposts", "impressions"))
        if has_engagement:
            lines.append(
                f"   ❤️ {post['likes']}  💬 {post['comments']}  🔁 {post['reposts']}  👁 {post['impressions']}"
            )
        else:
            lines.append("   _(no engagement data recorded)_")

        if post.get("post_url"):
            lines.append(f"   🔗 {post['post_url']}")

        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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
    app.add_handler(CommandHandler("evidence", evidence_command))
    app.add_handler(CommandHandler("task", task_command))
    app.add_handler(CommandHandler("approvals", approvals_command))
    app.add_handler(CommandHandler("rollup", rollup_command))
    app.add_handler(CommandHandler("spawn", spawn_command))
    app.add_handler(CommandHandler("preferences", preferences_command))
    app.add_handler(CommandHandler("voice", voice_command))
    app.add_handler(CommandHandler("platform", platform_command))
    app.add_handler(CommandHandler("xcreds", xcreds_command))
    app.add_handler(CommandHandler("connect", connect_command))
    app.add_handler(CommandHandler("digest", digest_command))
    app.add_handler(CommandHandler("published", published_command))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("APEX Telegram bot running. Press Ctrl+C to stop.")
    app.run_polling()


if __name__ == "__main__":
    main()
