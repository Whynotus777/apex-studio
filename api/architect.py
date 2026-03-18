"""
api/architect.py — TinkerArchitect: conversational team-building engine.

Manages multi-turn conversation between user and Tinker's architect.
Streams responses as SSE events.

Model priority:
  1. ANTHROPIC_API_KEY   → Claude Sonnet (claude-sonnet-4-6)
  2. GEMINI_API_KEY or
     GOOGLE_API_KEY      → Gemini 2.5 Flash Lite Preview (google-generativeai)
  3. neither             → keyword fallback (no API call)
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

try:
    import anthropic
    _ANTHROPIC_AVAILABLE = True
except ImportError:
    _ANTHROPIC_AVAILABLE = False

try:
    import google.generativeai as genai
    _GEMINI_AVAILABLE = True
except ImportError:
    _GEMINI_AVAILABLE = False

try:
    from api.architect_prompts import build_system_prompt
except ImportError:
    from architect_prompts import build_system_prompt  # type: ignore[no-redef]

APEX_HOME = Path(__file__).resolve().parents[1]

# ── Role icons (shared between LLM and fallback paths) ────────────────
_ROLE_ICONS: dict[str, str] = {
    "discovery": "🔭",
    "creation": "✍️",
    "quality_gate": "🛡️",
    "publishing_ops": "📅",
    "enrichment": "📊",
    "intelligence": "🔍",
    "orchestrator": "⚡",
    "custom": "🤖",
}

# ── Keyword synonyms for offline fallback ─────────────────────────────
_FALLBACK_SYNONYMS: dict[str, list[str]] = {
    "content-engine": [
        "content", "linkedin", "post", "posts", "social", "write",
        "draft", "marketing", "blog", "newsletter", "tweet",
    ],
    "sales-outreach": [
        "sales", "outreach", "lead", "leads", "email", "prospect",
        "prospects", "cold", "crm", "customer", "clients",
    ],
    "research-assistant": [
        "research", "analyze", "brief", "intelligence", "report",
        "summarize", "investigate", "study",
    ],
    "investor-research": [
        "investor", "investors", "vc", "vcs", "fundraise", "capital",
        "pitch", "seed", "angel", "raise",
    ],
    "competitive-intel": [
        "competitor", "competitors", "competitive", "monitor",
        "market", "tracking", "benchmark",
    ],
    "gtm-engine": [
        "gtm", "positioning", "launch", "campaign", "distribution",
        "messaging", "cmo",
    ],
    "daily-briefing": [
        "briefing", "digest", "morning", "daily", "news",
    ],
}


def _sse(data: dict[str, Any]) -> str:
    """Format a dict as a complete SSE event string."""
    return f"data: {json.dumps(data)}\n\n"


class TinkerArchitect:
    """
    LLM-powered conversation that understands user goals,
    recommends teams, asks follow-ups, and prepares launch config.
    """

    MODEL = "claude-sonnet-4-6"
    GEMINI_MODEL = "gemini-2.5-flash-lite-preview-06-17"

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (APEX_HOME / "db" / "apex_state.db")
        self.templates = self._load_all_templates()

        self._client: anthropic.AsyncAnthropic | None = None
        self._gemini_model: genai.GenerativeModel | None = None  # type: ignore[name-defined]

        gemini_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

        # Priority 1: Anthropic
        if _ANTHROPIC_AVAILABLE and os.environ.get("ANTHROPIC_API_KEY"):
            try:
                self._client = anthropic.AsyncAnthropic()
                print(f"[TinkerArchitect] model=claude/{self.MODEL}")
            except Exception as exc:
                print(f"[TinkerArchitect] Anthropic init failed: {exc}")
                self._client = None

        # Priority 2: Gemini (only if Anthropic wasn't initialised)
        if self._client is None and _GEMINI_AVAILABLE and gemini_key:
            try:
                genai.configure(api_key=gemini_key)
                self._gemini_model = genai.GenerativeModel(
                    model_name=self.GEMINI_MODEL,
                    system_instruction=self._build_system_prompt(),
                )
                print(f"[TinkerArchitect] model=gemini/{self.GEMINI_MODEL}")
            except Exception as exc:
                print(f"[TinkerArchitect] Gemini init failed: {exc}")
                self._gemini_model = None

        # Priority 3: no key available
        if self._client is None and self._gemini_model is None:
            print("[TinkerArchitect] No API key found — keyword fallback active")

    @property
    def has_llm(self) -> bool:
        return self._client is not None or self._gemini_model is not None

    # ── DB helpers ────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _ensure_table(self) -> None:
        # chat_sessions is defined in db/schema.sql; this is a no-op safety net.
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'collecting',
                    goal TEXT,
                    recommended_template_id TEXT,
                    workspace_id TEXT,
                    conversation_json TEXT NOT NULL DEFAULT '[]',
                    meta TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now')),
                    launched_at TEXT
                )
            """)

    def create_session(self) -> str:
        """Create a new chat session and return its ID."""
        self._ensure_table()
        session_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO chat_sessions "
                "(id, user_id, status, conversation_json, meta) "
                "VALUES (?, '', 'collecting', '[]', '{}')",
                (session_id,),
            )
        return session_id

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Return session dict with parsed messages, or None if not found."""
        self._ensure_table()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, status, goal, recommended_template_id, "
                "workspace_id, conversation_json, meta, created_at, updated_at "
                "FROM chat_sessions WHERE id = ?",
                (session_id,),
            ).fetchall()
        if not rows:
            return None
        row = dict(rows[0])
        # Normalise column names for callers
        row["messages"] = json.loads(row.get("conversation_json") or "[]")
        meta_raw = row.get("meta") or "{}"
        try:
            row["launch_config"] = json.loads(meta_raw)
        except Exception:
            row["launch_config"] = {}
        return row

    def _save_messages(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE chat_sessions "
                "SET conversation_json = ?, updated_at = datetime('now') WHERE id = ?",
                (json.dumps(messages), session_id),
            )

    def _save_recommendation(
        self,
        session_id: str,
        template_id: str,
        launch_config: dict[str, Any] | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE chat_sessions "
                "SET recommended_template_id = ?, meta = ?, "
                "updated_at = datetime('now') WHERE id = ?",
                (
                    template_id,
                    json.dumps(launch_config or {}),
                    session_id,
                ),
            )

    def mark_launched(self, session_id: str, workspace_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE chat_sessions "
                "SET workspace_id = ?, status = 'launched', "
                "launched_at = datetime('now'), updated_at = datetime('now') "
                "WHERE id = ?",
                (workspace_id, session_id),
            )

    # ── Template loading ──────────────────────────────────────────────

    def _load_all_templates(self) -> list[dict[str, Any]]:
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
                # Both "id" (for architect_prompts) and "_id" (internal) set
                manifest["id"] = entry.name
                manifest["_id"] = entry.name
                result.append(manifest)
            except Exception:
                continue
        return result

    # ── System prompt ─────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        return build_system_prompt(self.templates)

    # ── Structured block extraction ───────────────────────────────────

    def _extract_structured_blocks(self, response: str) -> list[dict[str, Any]]:
        """
        Parse the LLM response for structured JSON blocks like:
        ```team_recommendation
        {...}
        ```
        Returns a list of {"block_type": str, "data": dict} items.
        """
        blocks: list[dict[str, Any]] = []
        pattern = re.compile(
            r"```(team_recommendation|follow_up_question|launch_ready)\s*\n(.*?)\n```",
            re.DOTALL,
        )
        for match in pattern.finditer(response):
            block_type = match.group(1)
            raw_json = match.group(2).strip()
            try:
                data = json.loads(raw_json)
                blocks.append({"block_type": block_type, "data": data})
            except json.JSONDecodeError:
                # Malformed JSON — skip rather than crash
                continue
        return blocks

    # ── Keyword fallback (no API key) ─────────────────────────────────

    def _keyword_fallback_response(
        self, messages: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """
        Simple keyword match used when ANTHROPIC_API_KEY is absent.
        Returns {"text": str, "blocks": list}.
        """
        goal = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                goal = str(m.get("content", ""))
                break

        tokens = set(re.findall(r"[a-z]{3,}", goal.lower()))
        best_id: str | None = None
        best_score = 0
        for tid, kws in _FALLBACK_SYNONYMS.items():
            score = sum(1 for k in kws if k in tokens)
            if score > best_score:
                best_score = score
                best_id = tid

        manifest = next(
            (t for t in self.templates if t.get("_id") == best_id), None
        )
        if not manifest or best_score == 0:
            return {
                "text": (
                    "I'd love to help. Could you tell me a bit more — "
                    "are you looking for help with content, research, sales "
                    "outreach, or something else?"
                ),
                "blocks": [],
            }

        name = manifest.get("name", best_id)
        roles = []
        for agent in manifest.get("agents", []):
            desc = str(agent.get("description", ""))
            short = desc.split("—")[0].split(".")[0].strip()[:80]
            roles.append({
                "name": str(agent.get("name", "")).capitalize(),
                "icon": _ROLE_ICONS.get(str(agent.get("role", "")), "🤖"),
                "description": short or str(agent.get("role", "")).replace("_", " ").title(),
            })

        rec_block: dict[str, Any] = {
            "block_type": "team_recommendation",
            "data": {
                "template_id": best_id,
                "name": name,
                "why": (
                    f"Based on your goal, the {name} is the right fit. "
                    "This team works continuously on your behalf."
                ),
                "roles": roles,
                "pipeline": " → ".join(manifest.get("pipeline", [])),
            },
        }
        autonomy_block: dict[str, Any] = {
            "block_type": "follow_up_question",
            "data": {
                "id": "autonomy",
                "question": "How hands-on do you want to be?",
                "options": [
                    {
                        "value": "hands_on",
                        "label": "Review everything before it goes out",
                    },
                    {
                        "value": "managed",
                        "label": "Only flag issues — auto-approve good work",
                    },
                    {
                        "value": "autopilot",
                        "label": "Run fully on autopilot",
                    },
                ],
            },
        }
        return {
            "text": f"Based on what you've described, I'd recommend the **{name}**.",
            "blocks": [rec_block, autonomy_block],
        }

    # ── Main chat method ──────────────────────────────────────────────

    async def chat(
        self,
        session_id: str,
        user_message: str,
        documents: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Send a message in the conversation. Yields SSE event strings.

        SSE event shapes:
          {"type": "text_delta", "content": "..."}     — streaming text token
          {"type": "structured", "block_type": "...",   — parsed JSON block
           "data": {...}}
          {"type": "error", "content": "..."}           — error, stream aborted
          {"type": "done"}                              — stream complete
        """
        self._ensure_table()
        session = self.get_session(session_id)
        if not session:
            yield _sse({"type": "error", "content": "Session not found"})
            return

        messages: list[dict[str, Any]] = list(session.get("messages") or [])
        messages.append({"role": "user", "content": user_message})

        # ── Fallback path: no API key ─────────────────────────────────
        if not self.has_llm:
            result = self._keyword_fallback_response(messages)
            yield _sse({"type": "text_delta", "content": result["text"]})
            for block in result["blocks"]:
                yield _sse({
                    "type": "structured",
                    "block_type": block["block_type"],
                    "data": block["data"],
                })
                if block["block_type"] == "team_recommendation":
                    self._save_recommendation(
                        session_id,
                        str(block["data"].get("template_id", "")),
                        None,
                    )
            messages.append({"role": "assistant", "content": result["text"]})
            self._save_messages(session_id, messages)
            yield _sse({"type": "done"})
            return

        # ── Shared helper: emit structured blocks + persist ───────────
        # Called after whichever LLM path finishes accumulating full_response.

        async def _emit_blocks_and_persist(full_response: str) -> None:
            blocks = self._extract_structured_blocks(full_response)
            for block in blocks:
                yield _sse({
                    "type": "structured",
                    "block_type": block["block_type"],
                    "data": block["data"],
                })
                if block["block_type"] in ("team_recommendation", "launch_ready"):
                    template_id = str(block["data"].get("template_id", ""))
                    launch_config = (
                        block["data"].get("config")
                        if block["block_type"] == "launch_ready"
                        else None
                    )
                    if template_id:
                        self._save_recommendation(session_id, template_id, launch_config)
            messages.append({"role": "assistant", "content": full_response})
            self._save_messages(session_id, messages)

        # `_emit_blocks_and_persist` is defined as a local async generator so
        # we can yield from it in both LLM branches below.

        # Inject document context into the final user turn (shared by both paths)
        last_content = user_message
        if documents:
            doc_lines = "\n".join(
                f"[Document: {d.get('name', 'file')} — "
                f"{d.get('summary') or str(d.get('content', ''))[:200]}]"
                for d in documents
            )
            last_content = f"{user_message}\n\n{doc_lines}"

        # ── Gemini path ────────────────────────────────────────────────
        if self._gemini_model is not None and self._client is None:
            # Convert prior turns to Gemini's history format.
            # Gemini uses "model" for assistant turns, not "assistant".
            gemini_history: list[dict[str, Any]] = []
            for m in messages[:-1]:
                role = "user" if m.get("role") == "user" else "model"
                gemini_history.append({"role": role, "parts": [str(m.get("content", ""))]})

            full_response = ""
            try:
                chat_session = self._gemini_model.start_chat(history=gemini_history)
                response = await chat_session.send_message_async(
                    last_content, stream=True
                )
                async for chunk in response:
                    text_chunk = getattr(chunk, "text", "") or ""
                    if text_chunk:
                        full_response += text_chunk
                        yield _sse({"type": "text_delta", "content": text_chunk})
            except Exception as exc:
                yield _sse({"type": "error", "content": str(exc)})
                return

            async for event in _emit_blocks_and_persist(full_response):
                yield event
            yield _sse({"type": "done"})
            return

        # ── Claude / Anthropic path ────────────────────────────────────
        system_prompt = self._build_system_prompt()

        # Build Claude message list (all prior turns + latest user message)
        claude_messages: list[dict[str, str]] = []
        for m in messages[:-1]:
            claude_messages.append({
                "role": str(m["role"]),
                "content": str(m["content"]),
            })
        claude_messages.append({"role": "user", "content": last_content})

        full_response = ""
        try:
            assert self._client is not None  # narrowing; has_llm already checked
            async with self._client.messages.stream(
                model=self.MODEL,
                max_tokens=2048,
                system=system_prompt,
                messages=claude_messages,
            ) as stream:
                async for text_chunk in stream.text_stream:
                    full_response += text_chunk
                    yield _sse({"type": "text_delta", "content": text_chunk})
        except Exception as exc:
            yield _sse({"type": "error", "content": str(exc)})
            return

        async for event in _emit_blocks_and_persist(full_response):
            yield event
        yield _sse({"type": "done"})
