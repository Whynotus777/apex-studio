"""Agent instance persistence layer.

An AgentInstance is a runtime record of a launched team member. One row is
written per agent per workspace at launch time and is available for UI display
and future runtime routing.

This module has zero coupling to the live runtime (pipeline.py, spawn scripts,
or template execution). It is pure storage + business rules.
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

_JSON_FIELDS = {"tools", "skills", "metadata"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_instances (
    id                TEXT PRIMARY KEY,
    workspace_id      TEXT NOT NULL,
    display_name      TEXT NOT NULL,
    role_key          TEXT,
    role_description  TEXT,
    tools             TEXT DEFAULT '[]',
    skills            TEXT DEFAULT '[]',
    pipeline_position INTEGER NOT NULL,
    enabled           INTEGER NOT NULL DEFAULT 1,
    source            TEXT NOT NULL DEFAULT 'template',
    metadata          TEXT DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_agent_instances_workspace_id
    ON agent_instances(workspace_id);
CREATE INDEX IF NOT EXISTS idx_agent_instances_position
    ON agent_instances(workspace_id, pipeline_position);
"""

_UPDATABLE_FIELDS = {
    "display_name",
    "role_key",
    "role_description",
    "tools",
    "skills",
    "pipeline_position",
    "enabled",
    "source",
    "metadata",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _ser(value: Any) -> str:
    return json.dumps(value if value is not None else [])


def _deser(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for field in _JSON_FIELDS:
        if field in d and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except (json.JSONDecodeError, TypeError):
                d[field] = {} if field == "metadata" else []
    d["enabled"] = bool(d.get("enabled", 1))
    return d


class AgentInstanceStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path or _DEFAULT_DB)
        self._migrate()

    # ── internal ──────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _migrate(self) -> None:
        with self._connect() as conn:
            for stmt in _SCHEMA.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError:
                        pass
            conn.commit()

    # ── public API ────────────────────────────────────────────────────────

    def create_agent_instance(
        self,
        workspace_id: str,
        display_name: str,
        role_key: str | None,
        role_description: str | None,
        tools: list,
        skills: list,
        pipeline_position: int,
        source: str = "template",
        enabled: bool = True,
        metadata: dict | None = None,
    ) -> dict[str, Any]:
        """Insert a single agent instance and return its full record."""
        instance_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_instances
                    (id, workspace_id, display_name, role_key, role_description,
                     tools, skills, pipeline_position, enabled, source,
                     metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    workspace_id,
                    display_name,
                    role_key,
                    role_description,
                    _ser(tools),
                    _ser(skills),
                    pipeline_position,
                    int(enabled),
                    source,
                    json.dumps(metadata or {}),
                    now,
                    now,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM agent_instances WHERE id = ?", (instance_id,)
            ).fetchone()
        return _deser(row)

    def bulk_create_from_draft(
        self,
        workspace_id: str,
        draft_agents: list[dict],
    ) -> list[dict[str, Any]]:
        """Create agent instances from a list of draft-agent dicts.

        Each dict should carry the same fields as a ``team_draft_agents`` row
        (display_name, role_key, role_description, tools, skills,
        pipeline_position, source, enabled, metadata).  Missing optional
        fields fall back to safe defaults.

        Returns the created instances in pipeline_position order.
        """
        now = _now()
        created_ids: list[str] = []
        with self._connect() as conn:
            for agent in draft_agents:
                instance_id = str(uuid.uuid4())
                tools = agent.get("tools") or []
                skills = agent.get("skills") or []
                metadata = agent.get("metadata") or {}
                conn.execute(
                    """
                    INSERT INTO agent_instances
                        (id, workspace_id, display_name, role_key, role_description,
                         tools, skills, pipeline_position, enabled, source,
                         metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        instance_id,
                        workspace_id,
                        agent.get("display_name", "Agent"),
                        agent.get("role_key"),
                        agent.get("role_description"),
                        _ser(tools) if not isinstance(tools, str) else tools,
                        _ser(skills) if not isinstance(skills, str) else skills,
                        int(agent.get("pipeline_position", 0)),
                        int(agent.get("enabled", 1)),
                        agent.get("source", "template"),
                        json.dumps(metadata) if not isinstance(metadata, str) else metadata,
                        now,
                        now,
                    ),
                )
                created_ids.append(instance_id)
            conn.commit()
            placeholders = ",".join("?" for _ in created_ids)
            rows = conn.execute(
                f"SELECT * FROM agent_instances WHERE id IN ({placeholders})"
                " ORDER BY pipeline_position ASC",
                created_ids,
            ).fetchall()
        return [_deser(r) for r in rows]

    def get_workspace_agents(self, workspace_id: str) -> list[dict[str, Any]]:
        """Return all agent instances for a workspace, ordered by pipeline position."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM agent_instances
                WHERE workspace_id = ?
                ORDER BY pipeline_position ASC
                """,
                (workspace_id,),
            ).fetchall()
        return [_deser(r) for r in rows]

    def update_agent_instance(
        self, agent_id: str, updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply a partial update to an existing agent instance.

        Only keys listed in ``_UPDATABLE_FIELDS`` are written.
        Raises ``KeyError`` if the instance does not exist.
        """
        safe = {k: v for k, v in updates.items() if k in _UPDATABLE_FIELDS}
        if not safe:
            raise ValueError("No updatable fields provided.")

        # Serialize JSON fields
        for field in _JSON_FIELDS & safe.keys():
            val = safe[field]
            if not isinstance(val, str):
                safe[field] = json.dumps(val)

        # Coerce enabled to int
        if "enabled" in safe:
            safe["enabled"] = int(safe["enabled"])

        safe["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in safe)
        values = list(safe.values()) + [agent_id]

        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE agent_instances SET {set_clause} WHERE id = ?", values
            )
            conn.commit()
            if cur.rowcount == 0:
                raise KeyError(f"Agent instance '{agent_id}' not found.")
            row = conn.execute(
                "SELECT * FROM agent_instances WHERE id = ?", (agent_id,)
            ).fetchone()
        return _deser(row)

    def delete_agent_instance(self, agent_id: str) -> None:
        """Delete an agent instance by id. No-ops if not found."""
        with self._connect() as conn:
            conn.execute("DELETE FROM agent_instances WHERE id = ?", (agent_id,))
            conn.commit()
