from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


class SessionMemory:
    """SQLite-backed session memory using the existing agent_sessions table."""

    def __init__(self, apex_home: str | Path) -> None:
        self.apex_home = Path(apex_home).resolve()
        self.db_path = self.apex_home / "db" / "apex_state.db"

    def save(self, agent_id: str, session_id: str, task_id: str | None, context: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_sessions (id, agent_name, task_id, context, last_active, status)
                VALUES (?, ?, ?, ?, datetime('now'), 'active')
                ON CONFLICT(id) DO UPDATE SET
                    agent_name = excluded.agent_name,
                    task_id = excluded.task_id,
                    context = excluded.context,
                    last_active = datetime('now'),
                    status = 'active'
                """,
                (session_id, agent_id, task_id, context),
            )
            conn.commit()

    def get_latest(self, agent_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, agent_name, task_id, context, created_at, last_active, status
                FROM agent_sessions
                WHERE agent_name = ?
                ORDER BY COALESCE(last_active, created_at) DESC, created_at DESC, rowid DESC
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
        return dict(row) if row is not None else {}

    def get_history(self, agent_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, agent_name, task_id, context, created_at, last_active, status
                FROM agent_sessions
                WHERE agent_name = ?
                ORDER BY COALESCE(last_active, created_at) DESC, created_at DESC, rowid DESC
                LIMIT ?
                """,
                (agent_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn


class WorkingMemory:
    """File-backed working memory using existing agent scratchpads."""

    def __init__(self, apex_home: str | Path) -> None:
        self.apex_home = Path(apex_home).resolve()
        self.base_dir = self.apex_home / "templates" / "startup-chief-of-staff" / "agents"

    def read(self, agent_id: str) -> str:
        return self._scratchpad_path(agent_id).read_text()

    def append(self, agent_id: str, content: str, session_id: str) -> None:
        path = self._scratchpad_path(agent_id)
        existing = path.read_text()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"--- {session_id} | {timestamp} ---\n{content.rstrip()}\n"
        prefix = "\n" if existing and not existing.endswith("\n\n") else ""
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{prefix}{entry}")

    def get_recent(self, agent_id: str, lines: int = 100) -> str:
        content = self.read(agent_id)
        split_lines = content.splitlines()
        if lines <= 0:
            return ""
        return "\n".join(split_lines[-lines:])

    def clear(self, agent_id: str) -> None:
        self._scratchpad_path(agent_id).write_text("")

    def _scratchpad_path(self, agent_id: str) -> Path:
        path = self.base_dir / agent_id / "workspace" / "scratchpad.md"
        if not path.exists():
            raise FileNotFoundError(f"Scratchpad not found for agent '{agent_id}': {path}")
        return path


class DurableMemory:
    """File-backed durable memory using the shared MEMORY.md document."""

    def __init__(self, apex_home: str | Path) -> None:
        self.apex_home = Path(apex_home).resolve()
        self.memory_path = (
            self.apex_home / "templates" / "startup-chief-of-staff" / "workspace" / "MEMORY.md"
        )

    def read(self) -> str:
        return self.memory_path.read_text()

    def append(self, content: str) -> None:
        normalized = content.rstrip()
        if not normalized:
            return
        existing = self.memory_path.read_text()
        prefix = "\n" if existing and not existing.endswith("\n") else ""
        with self.memory_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{prefix}{normalized}\n")

    def search(self, keyword: str) -> list[str]:
        if not keyword:
            return []
        needle = keyword.lower()
        return [line for line in self.read().splitlines() if needle in line.lower()]
