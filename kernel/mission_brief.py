from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path


class MissionBrief:
    """
    Persistent structured state for a team's ongoing work.
    V1: Created when a team launches. Read by agents at spawn.
    Updated manually via API — NOT auto-updated by agent runs yet.
    """

    _ALLOWED_UPDATE_FIELDS = {"objective", "definition_of_done", "constraints", "status"}

    def __init__(
        self,
        apex_home: str | Path | None = None,
        db_path: str | Path | None = None,
    ) -> None:
        if apex_home is None:
            apex_home = os.environ.get("APEX_HOME", str(Path(__file__).resolve().parents[1]))
        self.apex_home = Path(apex_home).resolve()
        self.db_path = Path(db_path or self.apex_home / "db" / "apex_state.db").resolve()
        self._ensure_table()

    def create_brief(
        self,
        workspace_id: str,
        objective: str,
        definition_of_done: str | None = None,
        constraints: list | None = None,
    ) -> dict:
        """Create initial brief when a team is launched."""
        if not workspace_id:
            raise ValueError("workspace_id is required.")
        if not objective:
            raise ValueError("objective is required.")
        constraints_json = json.dumps(constraints or [])
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO mission_briefs
                    (workspace_id, objective, status, constraints, definition_of_done, created_at)
                VALUES (?, ?, 'active', ?, ?, ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    objective          = excluded.objective,
                    constraints        = excluded.constraints,
                    definition_of_done = excluded.definition_of_done,
                    status             = 'active',
                    updated_at         = datetime('now')
                """,
                (workspace_id, objective, constraints_json, definition_of_done, now),
            )
            conn.commit()
        return self.get_brief(workspace_id)

    def get_brief(self, workspace_id: str) -> dict | None:
        """Returns the current mission brief, or None if not set."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT workspace_id, objective, status, constraints,
                       definition_of_done, created_at, updated_at
                FROM mission_briefs
                WHERE workspace_id = ?
                """,
                (workspace_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        try:
            result["constraints"] = json.loads(result["constraints"] or "[]")
        except (json.JSONDecodeError, TypeError):
            result["constraints"] = []
        return result

    def update_brief(self, workspace_id: str, updates: dict) -> dict:
        """Manually update fields. Called by API layer, not by agents."""
        if not updates:
            raise ValueError("updates must not be empty.")
        allowed = {k: v for k, v in updates.items() if k in self._ALLOWED_UPDATE_FIELDS}
        if not allowed:
            raise ValueError(
                f"No valid fields in updates. Allowed: {sorted(self._ALLOWED_UPDATE_FIELDS)}"
            )
        set_clauses: list[str] = []
        params: list = []
        for field, value in allowed.items():
            if field == "constraints":
                set_clauses.append("constraints = ?")
                params.append(json.dumps(value if isinstance(value, list) else []))
            else:
                set_clauses.append(f"{field} = ?")
                params.append(value)
        set_clauses.append("updated_at = datetime('now')")
        params.append(workspace_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE mission_briefs SET {', '.join(set_clauses)} WHERE workspace_id = ?",
                params,
            )
            conn.commit()
            if cur.rowcount == 0:
                raise ValueError(f"No brief found for workspace '{workspace_id}'.")
        return self.get_brief(workspace_id)

    def get_brief_summary(self, workspace_id: str) -> str | None:
        """
        Returns a concise text summary for injection into agent prompts.
        Returns None if no brief exists (agents work fine without it).

        Format:
        ## Mission Brief
        Objective: Find seed investors for an AI agent platform
        Constraints: Seed/Series A only. AI infrastructure thesis.
        Done when: Ranked list with outreach angles, all sources verified.
        """
        brief = self.get_brief(workspace_id)
        if brief is None:
            return None
        lines = ["## Mission Brief"]
        lines.append(f"Objective: {brief['objective']}")
        constraints = brief.get("constraints") or []
        if constraints:
            lines.append(f"Constraints: {'. '.join(str(c) for c in constraints)}")
        dod = brief.get("definition_of_done")
        if dod:
            lines.append(f"Done when: {dod}")
        return "\n".join(lines)

    def _ensure_table(self) -> None:
        """Create mission_briefs table if it does not exist."""
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS mission_briefs (
                    workspace_id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    constraints TEXT DEFAULT '[]',
                    definition_of_done TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
