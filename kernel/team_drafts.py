"""Team Draft persistence layer.

A TeamDraft is a pre-launch, user-editable team spec. The architect produces
one from a recommendation; the user adjusts roles/tools/settings; the launch
compiler turns it into a real workspace + agent instances.

This module has zero coupling to the live runtime (kernel/pipeline.py, spawn
scripts, or template execution). It is pure storage + business rules.
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

_VALID_STATUSES = {"draft", "ready", "launched"}
_JSON_DRAFT_FIELDS = {"channels", "metadata"}
_JSON_AGENT_FIELDS = {"tools", "skills", "metadata"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _ser(value: Any) -> str:
    """Serialize a Python object to a JSON string."""
    return json.dumps(value if value is not None else [])


def _deser_draft(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for field in _JSON_DRAFT_FIELDS:
        if field in d and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                d[field] = [] if field == "channels" else {}
    return d


def _deser_agent(row: sqlite3.Row) -> dict[str, Any]:
    a = dict(row)
    for field in _JSON_AGENT_FIELDS:
        if field in a and isinstance(a[field], str):
            try:
                a[field] = json.loads(a[field])
            except (json.JSONDecodeError, TypeError):
                a[field] = [] if field != "metadata" else {}
    a["enabled"] = bool(a.get("enabled", 1))
    return a


_SCHEMA = """
CREATE TABLE IF NOT EXISTS team_drafts (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT DEFAULT 'default',
    source_goal             TEXT NOT NULL,
    recommended_template_id TEXT,
    name                    TEXT,
    status                  TEXT NOT NULL DEFAULT 'draft',
    autonomy                TEXT DEFAULT 'hands_on',
    update_cadence          TEXT DEFAULT 'after_each_step',
    channels                TEXT DEFAULT '[]',
    metadata                TEXT DEFAULT '{}',
    created_at              TEXT NOT NULL,
    updated_at              TEXT
);

CREATE TABLE IF NOT EXISTS team_draft_agents (
    id                TEXT PRIMARY KEY,
    draft_id          TEXT NOT NULL,
    role_key          TEXT,
    display_name      TEXT NOT NULL,
    role_description  TEXT,
    tools             TEXT DEFAULT '[]',
    skills            TEXT DEFAULT '[]',
    pipeline_position INTEGER NOT NULL,
    enabled           INTEGER NOT NULL DEFAULT 1,
    source            TEXT NOT NULL DEFAULT 'template',
    metadata          TEXT DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT,
    FOREIGN KEY (draft_id) REFERENCES team_drafts(id)
);

CREATE INDEX IF NOT EXISTS idx_team_draft_agents_draft_id
    ON team_draft_agents(draft_id);

CREATE INDEX IF NOT EXISTS idx_team_draft_agents_position
    ON team_draft_agents(draft_id, pipeline_position);
"""


class TeamDraftStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = str(db_path or _DEFAULT_DB)
        self._ensure_tables()

    # ── connection ────────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    def _ensure_tables(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    # ── Draft CRUD ────────────────────────────────────────────────────────────

    def create_draft(
        self,
        user_id: str = "default",
        source_goal: str = "",
        recommended_template_id: str | None = None,
        name: str | None = None,
        autonomy: str = "hands_on",
        update_cadence: str = "after_each_step",
        channels: list | None = None,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        if not source_goal:
            raise ValueError("source_goal is required.")
        draft_id = f"draft-{uuid.uuid4().hex[:12]}"
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO team_drafts
                    (id, user_id, source_goal, recommended_template_id, name,
                     status, autonomy, update_cadence, channels, metadata,
                     created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    draft_id, user_id, source_goal, recommended_template_id, name,
                    "draft", autonomy, update_cadence,
                    _ser(channels or []), _ser(metadata or {}),
                    now, now,
                ),
            )
            conn.commit()
        return self.get_draft(draft_id)  # type: ignore[return-value]

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_drafts WHERE id = ?", (draft_id,)
            ).fetchone()
        return _deser_draft(row) if row else None

    def list_drafts(
        self,
        user_id: str = "default",
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM team_drafts WHERE user_id = ?"
        params: list[Any] = [user_id]
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_deser_draft(r) for r in rows]

    def update_draft(self, draft_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "name", "autonomy", "update_cadence", "channels",
            "metadata", "recommended_template_id", "source_goal",
        }
        fields = {k: v for k, v in updates.items() if k in allowed}
        if not fields:
            raise ValueError(f"No updatable fields in: {list(updates)}")
        # serialize JSON fields
        for f in _JSON_DRAFT_FIELDS:
            if f in fields:
                fields[f] = _ser(fields[f])
        fields["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [draft_id]
        with self._connect() as conn:
            conn.execute(
                f"UPDATE team_drafts SET {set_clause} WHERE id = ?", values
            )
            conn.commit()
        result = self.get_draft(draft_id)
        if result is None:
            raise ValueError(f"Draft '{draft_id}' not found.")
        return result

    def set_status(self, draft_id: str, status: str) -> None:
        if status not in _VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Must be one of {_VALID_STATUSES}.")
        with self._connect() as conn:
            conn.execute(
                "UPDATE team_drafts SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), draft_id),
            )
            conn.commit()

    def delete_draft(self, draft_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM team_draft_agents WHERE draft_id = ?", (draft_id,))
            conn.execute("DELETE FROM team_drafts WHERE id = ?", (draft_id,))
            conn.commit()

    # ── Draft Agent CRUD ──────────────────────────────────────────────────────

    def add_draft_agent(
        self,
        draft_id: str,
        role_key: str | None,
        display_name: str,
        role_description: str | None,
        tools: list,
        skills: list,
        pipeline_position: int,
        source: str = "template",
        enabled: bool = True,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        if not display_name:
            raise ValueError("display_name is required.")
        agent_id = f"da-{uuid.uuid4().hex[:12]}"
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO team_draft_agents
                    (id, draft_id, role_key, display_name, role_description,
                     tools, skills, pipeline_position, enabled, source,
                     metadata, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    agent_id, draft_id, role_key, display_name, role_description,
                    _ser(tools), _ser(skills), pipeline_position,
                    1 if enabled else 0, source,
                    _ser(metadata or {}), now, now,
                ),
            )
            conn.commit()
        return self._get_agent(agent_id)  # type: ignore[return-value]

    def get_draft_agents(self, draft_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM team_draft_agents
                WHERE draft_id = ?
                ORDER BY pipeline_position ASC, created_at ASC
                """,
                (draft_id,),
            ).fetchall()
        return [_deser_agent(r) for r in rows]

    def update_draft_agent(self, agent_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "display_name", "role_description", "tools", "skills",
            "pipeline_position", "enabled", "source", "metadata", "role_key",
        }
        fields = {k: v for k, v in updates.items() if k in allowed}
        if not fields:
            raise ValueError(f"No updatable fields in: {list(updates)}")
        for f in _JSON_AGENT_FIELDS:
            if f in fields:
                fields[f] = _ser(fields[f])
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        fields["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [agent_id]
        with self._connect() as conn:
            conn.execute(
                f"UPDATE team_draft_agents SET {set_clause} WHERE id = ?", values
            )
            conn.commit()
        result = self._get_agent(agent_id)
        if result is None:
            raise ValueError(f"Draft agent '{agent_id}' not found.")
        return result

    def delete_draft_agent(self, agent_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM team_draft_agents WHERE id = ?", (agent_id,))
            conn.commit()

    def reorder_draft_agents(
        self, draft_id: str, ordered_agent_ids: list[str]
    ) -> list[dict[str, Any]]:
        """Rewrite pipeline_position for every agent in draft_id.

        Agents in ordered_agent_ids get positions 1..N in that order.
        Agents present in the draft but missing from the list are appended
        at the end in their current order (they are not lost).
        """
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM team_draft_agents WHERE draft_id = ? ORDER BY pipeline_position ASC",
                (draft_id,),
            ).fetchall()
            existing_ids = [r["id"] for r in existing]

            # Build final ordered list: specified ids first, then any remainder
            seen = set(ordered_agent_ids)
            remainder = [eid for eid in existing_ids if eid not in seen]
            final_order = [aid for aid in ordered_agent_ids if aid in set(existing_ids)] + remainder

            now = _now()
            for pos, agent_id in enumerate(final_order, start=1):
                conn.execute(
                    "UPDATE team_draft_agents SET pipeline_position = ?, updated_at = ? WHERE id = ?",
                    (pos, now, agent_id),
                )
            conn.commit()
        return self.get_draft_agents(draft_id)

    # ── internal ──────────────────────────────────────────────────────────────

    def _get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM team_draft_agents WHERE id = ?", (agent_id,)
            ).fetchone()
        return _deser_agent(row) if row else None
