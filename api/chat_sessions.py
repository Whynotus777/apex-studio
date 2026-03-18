from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from kernel.api import ApexKernel


class ChatSessionManager:
    """
    Manages chat sessions between users and Tinker's architect.
    Sessions persist conversation history so users can return to them.
    """

    _STATE_ORDER = [
        "collecting",
        "recommending",
        "awaiting_confirmation",
        "launch_ready",
        "launched",
    ]

    def __init__(
        self,
        apex_home: str | Path | None = None,
        db_path: str | Path | None = None,
        kernel: ApexKernel | None = None,
    ) -> None:
        self.kernel = kernel or ApexKernel(apex_home=apex_home, db_path=db_path)
        self.apex_home = self.kernel.apex_home
        self.db_path = self.kernel.db_path
        self.kernel._ensure_chat_sessions_table()

    def create_session(self, user_id: str = "default") -> dict[str, Any]:
        """Create a new chat session. Returns { session_id, created_at }."""
        return self.kernel.create_chat_session(user_id)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Get session with full message history."""
        try:
            return self.kernel.get_chat_session(session_id)
        except ValueError:
            return None

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append a message to the session. Returns the updated session."""
        session = self._require_session(session_id)
        messages = list(session.get("messages", []))
        messages.append(
            {
                "id": f"msg-{uuid.uuid4().hex[:10]}",
                "role": role,
                "content": content,
                "metadata": metadata or {},
                "created_at": self._db_now(),
            }
        )
        goal = session.get("goal")
        if role == "user" and not goal:
            goal = content.strip()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chat_sessions
                SET goal = ?, conversation_json = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (goal, json.dumps(messages), session_id),
            )
            conn.commit()
        return self.kernel.get_chat_session(session_id)

    def set_recommendation(self, session_id: str, template_id: str) -> None:
        """Store which template was recommended (for launch)."""
        self._require_session(session_id)
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE chat_sessions
                SET recommended_template_id = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (template_id, session_id),
            )
            conn.commit()

    def launch_from_session(
        self,
        session_id: str,
        name: str,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Launch a team from a chat session.
        ONLY allowed when session status is 'launch_ready'.

        1. Verify session status is 'launch_ready'
        2. Get the recommended template
        3. Call kernel.launch_template()
        4. Create mission brief from the conversation goal
        5. Link any uploaded documents to the workspace
        6. Update session status to 'launched' and store workspace_id
        Returns { workspace_id, team_name }
        Raises ValueError if session is not in launch_ready state.
        """
        session = self._require_session(session_id)
        if session.get("status") != "launch_ready":
            raise ValueError(f"Chat session '{session_id}' is not ready to launch.")

        template_id = str(session.get("recommended_template_id") or "").strip()
        if not template_id:
            raise ValueError(f"Chat session '{session_id}' has no recommended template.")

        launch_result = self.kernel.launch_template(template_id, name=name, overrides=config or {})
        workspace_id = str(launch_result["workspace_id"])
        workspace = self.kernel.get_workspace(workspace_id)
        team_name = str(workspace.get("name") or name)

        mission_task_id = self._create_mission_brief(
            workspace_id=workspace_id,
            template_id=template_id,
            session=session,
        )
        self._link_documents_to_workspace(session_id, workspace_id)

        with self._connect() as conn:
            meta = dict(session.get("meta", {}))
            if mission_task_id:
                meta["mission_task_id"] = mission_task_id
            conn.execute(
                """
                UPDATE chat_sessions
                SET status = 'launched',
                    workspace_id = ?,
                    meta = ?,
                    launched_at = datetime('now'),
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (workspace_id, json.dumps(meta), session_id),
            )
            conn.commit()

        return {"workspace_id": workspace_id, "team_name": team_name}

    def update_status(self, session_id: str, status: str) -> None:
        """
        Update session status. Must follow the state machine:
        collecting → recommending → awaiting_confirmation → launch_ready → launched
        Raises ValueError on invalid transitions.
        """
        session = self._require_session(session_id)
        current = str(session.get("status") or "collecting")
        target = status.strip()
        if not target:
            raise ValueError("Status is required.")
        if not self._is_valid_transition(current, target):
            raise ValueError(f"Invalid chat session transition: {current} -> {target}")
        with self._connect() as conn:
            conn.execute(
                "UPDATE chat_sessions SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (target, session_id),
            )
            conn.commit()

    def get_active_sessions(self, user_id: str = "default") -> list[dict[str, Any]]:
        """Get sessions that haven't been launched or abandoned yet."""
        return self.kernel.get_active_chat_sessions(user_id)

    def _create_mission_brief(
        self,
        workspace_id: str,
        template_id: str,
        session: dict[str, Any],
    ) -> str | None:
        goal = str(session.get("goal") or "").strip()
        if not goal:
            return None

        goal_id = self._first_active_goal_id()
        if goal_id is None:
            goal_id = "goal-chat-sessions"
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO goals (id, name, description, status, created_at)
                    VALUES (?, 'Chat Session Missions', 'Auto-created goals from architect chat sessions.', 'active', datetime('now'))
                    """,
                    (goal_id,),
                )
                conn.commit()

        title = goal[:117] + "..." if len(goal) > 120 else goal
        conversation_lines = []
        for message in session.get("messages", []):
            role = str(message.get("role", "unknown")).strip()
            content = str(message.get("content", "")).strip()
            if content:
                conversation_lines.append(f"{role}: {content}")
        description = "Mission brief from architect chat session"
        if conversation_lines:
            description += ":\n\n" + "\n".join(conversation_lines)

        task_id = self.kernel.create_task(
            {
                "goal_id": goal_id,
                "title": title or "Launch a team from architect session",
                "description": description,
                "status": "backlog",
            }
        )
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET workspace_id = ? WHERE id = ?",
                (workspace_id, task_id),
            )
            conn.commit()

        start_agent = self._resolve_start_agent(workspace_id, template_id)
        if start_agent:
            try:
                self.kernel.assign_task(task_id, start_agent)
            except Exception:
                pass
        return task_id

    def _resolve_start_agent(self, workspace_id: str, template_id: str) -> str | None:
        manifest = self.kernel.get_template(template_id)
        pipeline = manifest.get("pipeline", [])
        if not pipeline:
            return None
        stage = str(pipeline[0]).lower().strip()
        stage_map = {
            "discover": "scout",
            "analyze": "analyst",
            "analyse": "analyst",
            "strategize": "strategist",
            "create": "writer",
            "draft": "writer",
            "review": "critic",
            "validate": "critic",
            "publish": "publisher",
            "build": "builder",
            "launch": "apex",
            "grow": "apex",
            "enrich": "analyst",
        }
        role = stage_map.get(stage)
        if not role:
            return None
        candidate = f"{workspace_id}-{role}"
        try:
            self.kernel._ensure_agent_exists(candidate)
            return candidate
        except ValueError:
            return None

    def _link_documents_to_workspace(self, session_id: str, workspace_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workspace_documents
                SET workspace_id = ?
                WHERE chat_session_id = ? AND (workspace_id IS NULL OR workspace_id = '')
                """,
                (workspace_id, session_id),
            )
            conn.commit()

    def _first_active_goal_id(self) -> str | None:
        rows = self.kernel._fetch_all(
            "SELECT id FROM goals WHERE status = 'active' ORDER BY created_at ASC LIMIT 1"
        )
        return str(rows[0]["id"]) if rows else None

    def _require_session(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        if session is None:
            raise ValueError(f"Chat session '{session_id}' does not exist.")
        return session

    def _is_valid_transition(self, current: str, target: str) -> bool:
        if current == target:
            return True
        if target == "abandoned":
            return current != "launched"
        if current == "launched":
            return False
        if target not in self._STATE_ORDER:
            return False
        try:
            return self._STATE_ORDER.index(target) == self._STATE_ORDER.index(current) + 1
        except ValueError:
            return False

    def _db_now(self) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT datetime('now') AS ts").fetchone()
        return str(row["ts"])

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
