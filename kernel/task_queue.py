"""Task queue — explicit serialization of foreground pipeline runs per workspace.

Only ONE pipeline may be active per workspace at a time. When a new task
arrives while a pipeline is already running, it is enqueued as 'queued'.
Auto-advancing the queue on completion is Wave 2 — not implemented here.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TaskQueue:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._migrate()

    # ── internal ──────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_queue (
                    id             TEXT PRIMARY KEY,
                    workspace_id   TEXT NOT NULL,
                    task_id        TEXT NOT NULL,
                    queue_position INTEGER NOT NULL,
                    queue_state    TEXT NOT NULL DEFAULT 'queued',
                    enqueued_at    TEXT NOT NULL,
                    started_at     TEXT,
                    completed_at   TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_queue_workspace "
                "ON task_queue(workspace_id, queue_state)"
            )
            conn.commit()

    def _next_position(self, conn: sqlite3.Connection, workspace_id: str) -> int:
        row = conn.execute(
            "SELECT MAX(queue_position) AS mx FROM task_queue WHERE workspace_id = ?",
            (workspace_id,),
        ).fetchone()
        return (row["mx"] or 0) + 1

    # ── public API ────────────────────────────────────────────────────────

    def enqueue_task(self, workspace_id: str, task_id: str) -> str:
        """Insert a new entry in 'queued' state. Returns the queue entry id."""
        entry_id = str(uuid.uuid4())
        with self._connect() as conn:
            pos = self._next_position(conn, workspace_id)
            conn.execute(
                """
                INSERT INTO task_queue
                    (id, workspace_id, task_id, queue_position, queue_state, enqueued_at)
                VALUES (?, ?, ?, ?, 'queued', ?)
                """,
                (entry_id, workspace_id, task_id, pos, _now()),
            )
            conn.commit()
        return entry_id

    def team_has_active_run(self, workspace_id: str) -> bool:
        """Return True if there is currently an 'active' entry for this workspace."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM task_queue WHERE workspace_id = ? AND queue_state = 'active' LIMIT 1",
                (workspace_id,),
            ).fetchone()
        return row is not None

    def next_runnable_task(self, workspace_id: str) -> Optional[str]:
        """Return the task_id of the lowest-position 'queued' entry, or None."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT task_id FROM task_queue
                WHERE workspace_id = ? AND queue_state = 'queued'
                ORDER BY queue_position ASC
                LIMIT 1
                """,
                (workspace_id,),
            ).fetchone()
        return row["task_id"] if row else None

    def mark_active(self, task_id: str) -> None:
        """Transition a queued entry to 'active'."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_queue
                SET queue_state = 'active', started_at = ?
                WHERE task_id = ? AND queue_state = 'queued'
                """,
                (_now(), task_id),
            )
            conn.commit()

    def mark_completed(self, task_id: str) -> None:
        """Transition an active entry to 'completed'."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE task_queue
                SET queue_state = 'completed', completed_at = ?
                WHERE task_id = ? AND queue_state = 'active'
                """,
                (_now(), task_id),
            )
            conn.commit()

    def get_queue(self, workspace_id: str) -> list[dict]:
        """Return all queue entries for a workspace, ordered by position."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, workspace_id, task_id, queue_position, queue_state,
                       enqueued_at, started_at, completed_at
                FROM task_queue
                WHERE workspace_id = ?
                ORDER BY queue_position ASC
                """,
                (workspace_id,),
            ).fetchall()
        return [dict(r) for r in rows]
