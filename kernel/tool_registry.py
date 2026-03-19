"""Tool registry — user-facing catalogue of available tools.

Every tool that an agent (or a team draft agent) can be granted access to
should be registered here.  The registry is the source of truth for the
builder UI's "grant tools" picker.

This module has no coupling to kernel/pipeline.py, spawn scripts, or any
live runtime.  It is pure storage + a seed list.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_APEX_HOME = Path(__file__).resolve().parents[1]
_DEFAULT_DB = _APEX_HOME / "db" / "apex_state.db"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ── Default tool definitions ──────────────────────────────────────────────────

_DEFAULTS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": "Search the web for current information, news, and research.",
        "category": "research",
    },
    {
        "name": "file_read",
        "description": "Read files from the workspace (documents, notes, configs).",
        "category": "storage",
    },
    {
        "name": "file_write",
        "description": "Write or update files in the workspace.",
        "category": "storage",
    },
    {
        "name": "slack_send",
        "description": "Send messages and notifications to Slack channels.",
        "category": "communication",
    },
    {
        "name": "telegram_send",
        "description": "Send messages to a Telegram chat or channel.",
        "category": "communication",
    },
    {
        "name": "linkedin_post",
        "description": "Publish posts and articles to LinkedIn.",
        "category": "publishing",
    },
    {
        "name": "twitter_post",
        "description": "Post tweets and threads to X (Twitter).",
        "category": "publishing",
    },
    {
        "name": "github_issue",
        "description": "Create and update GitHub issues on connected repositories.",
        "category": "engineering",
    },
    {
        "name": "github_pr_comment",
        "description": "Post review comments on GitHub pull requests.",
        "category": "engineering",
    },
    {
        "name": "document_search",
        "description": "Search and retrieve content from uploaded documents.",
        "category": "research",
    },
]


# ── Schema ────────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_registry (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    description   TEXT NOT NULL,
    category      TEXT NOT NULL,
    config_schema TEXT DEFAULT '{}',
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT
);
"""


# ── ToolRegistry ──────────────────────────────────────────────────────────────

class ToolRegistry:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or _DEFAULT_DB)
        self._ensure_table()

    # ── connection ────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _ensure_table(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    # ── helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _deser(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        raw = d.get("config_schema")
        if isinstance(raw, str):
            try:
                d["config_schema"] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                d["config_schema"] = {}
        d["enabled"] = bool(d.get("enabled", 1))
        return d

    # ── public API ────────────────────────────────────────────────────────────

    def seed_defaults(self) -> None:
        """Insert all default tools if they do not yet exist (idempotent)."""
        with self._connect() as conn:
            for t in _DEFAULTS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO tool_registry
                        (id, name, description, category, config_schema,
                         enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        f"tool-{uuid.uuid4().hex[:12]}",
                        t["name"],
                        t["description"],
                        t["category"],
                        json.dumps(t.get("config_schema", {})),
                        _now(),
                        _now(),
                    ),
                )
            conn.commit()

    def list_tools(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        """Return all tools, optionally filtered to enabled-only."""
        sql = "SELECT * FROM tool_registry"
        params: list[Any] = []
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY category ASC, name ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._deser(r) for r in rows]

    def get_tool(self, name: str) -> dict[str, Any] | None:
        """Return a tool by name, or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM tool_registry WHERE name = ?", (name,)
            ).fetchone()
        return self._deser(row) if row else None

    def register_tool(
        self,
        name: str,
        description: str,
        category: str,
        config_schema: dict | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Register a new tool. Raises ValueError if name already exists."""
        tool_id = f"tool-{uuid.uuid4().hex[:12]}"
        now = _now()
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO tool_registry
                        (id, name, description, category, config_schema,
                         enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tool_id, name, description, category,
                        json.dumps(config_schema or {}),
                        1 if enabled else 0,
                        now, now,
                    ),
                )
                conn.commit()
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Tool '{name}' is already registered.") from exc
        return self.get_tool(name)  # type: ignore[return-value]

    def update_tool(self, name: str, updates: dict[str, Any]) -> dict[str, Any]:
        """Update mutable fields on a tool. Returns the updated record."""
        allowed = {"description", "category", "config_schema", "enabled"}
        fields = {k: v for k, v in updates.items() if k in allowed}
        if not fields:
            raise ValueError(f"No updatable fields provided. Allowed: {allowed}")
        if "config_schema" in fields:
            fields["config_schema"] = json.dumps(fields["config_schema"])
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        fields["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [name]
        with self._connect() as conn:
            conn.execute(
                f"UPDATE tool_registry SET {set_clause} WHERE name = ?", values
            )
            conn.commit()
        result = self.get_tool(name)
        if result is None:
            raise ValueError(f"Tool '{name}' not found.")
        return result

    def disable_tool(self, name: str) -> None:
        """Mark a tool as disabled (hides it from builder UI)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE tool_registry SET enabled = 0, updated_at = ? WHERE name = ?",
                (_now(), name),
            )
            conn.commit()
