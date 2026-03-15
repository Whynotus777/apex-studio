from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from kernel.parse_response import normalize_status
except ImportError:
    from parse_response import normalize_status


class ApexKernel:
    """Stable Python wrapper over the existing APEX runtime."""

    def __init__(self, apex_home: str | Path | None = None, db_path: str | Path | None = None) -> None:
        self.apex_home = Path(apex_home or Path(__file__).resolve().parents[1]).resolve()
        self.db_path = Path(db_path or self.apex_home / "db" / "apex_state.db").resolve()
        self.kernel_dir = self.apex_home / "kernel"
        self.agents_dir = self.apex_home / "templates" / "startup-chief-of-staff" / "agents"
        self._migrate()

    def create_agent(self, config: dict[str, Any]) -> str:
        agent_id = str(config.get("name") or config.get("id") or "").strip()
        if not agent_id:
            raise ValueError("Agent config must include 'name' or 'id'.")

        agent_dir = self.agents_dir / agent_id
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT agent_name FROM agent_status WHERE agent_name = ?",
                (agent_id,),
            ).fetchone()
            if existing or agent_dir.exists():
                raise ValueError(f"Agent '{agent_id}' already exists.")

            agent_dir.joinpath("constraints").mkdir(parents=True, exist_ok=False)
            agent_dir.joinpath("workspace").mkdir(parents=True, exist_ok=False)

            agent_json = {
                "name": agent_id,
                "role": config.get("role", "custom"),
                "description": config.get("description", ""),
                "model": config.get("model", {"primary": "qwen3.5-apex", "fallback": "claude-sonnet"}),
                "heartbeat": config.get("heartbeat"),
                "heartbeat_description": config.get("heartbeat_description"),
                "capabilities": config.get("capabilities", []),
                "can_message": config.get("can_message", ["apex", "scout", "analyst", "builder", "critic"]),
                "api_config": config.get("api_config", {"think": False, "num_ctx": 4096, "temperature": 0.3}),
            }
            agent_dir.joinpath("agent.json").write_text(json.dumps(agent_json, indent=2) + "\n")
            agent_dir.joinpath("AGENTS.md").write_text(str(config.get("instructions", "")).strip() + "\n")
            agent_dir.joinpath("constraints", "hard-rules.md").write_text(
                self._normalize_markdown_lines(config.get("hard_rules", []))
            )
            agent_dir.joinpath("constraints", "soft-preferences.md").write_text(
                self._normalize_markdown_lines(config.get("soft_preferences", []))
            )
            agent_dir.joinpath("constraints", "anti-patterns.md").write_text(
                self._normalize_markdown_lines(config.get("anti_patterns", []))
            )
            agent_dir.joinpath("workspace", "scratchpad.md").write_text("")

            conn.execute(
                """
                INSERT INTO agent_status (agent_name, status, model_active, meta)
                VALUES (?, 'idle', ?, ?)
                """,
                (
                    agent_id,
                    agent_json["model"].get("primary", "qwen3.5-apex"),
                    json.dumps({"paused": False, "config_path": str(agent_dir / "agent.json")}),
                ),
            )
            conn.commit()

        return agent_id

    def pause_agent(self, agent_id: str) -> None:
        self._ensure_agent_exists(agent_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT meta FROM agent_status WHERE agent_name = ?",
                (agent_id,),
            ).fetchone()
            meta = self._load_json(row["meta"] if row else None)
            meta["paused"] = True
            conn.execute(
                "UPDATE agent_status SET status = 'paused', meta = ? WHERE agent_name = ?",
                (json.dumps(meta), agent_id),
            )
            conn.commit()

    def resume_agent(self, agent_id: str) -> None:
        self._ensure_agent_exists(agent_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT meta FROM agent_status WHERE agent_name = ?",
                (agent_id,),
            ).fetchone()
            meta = self._load_json(row["meta"] if row else None)
            meta["paused"] = False
            conn.execute(
                "UPDATE agent_status SET status = 'idle', meta = ? WHERE agent_name = ?",
                (json.dumps(meta), agent_id),
            )
            conn.commit()

    def get_agent_status(self, agent_id: str) -> dict[str, Any]:
        self._ensure_agent_exists(agent_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT agent_name, status, current_task, last_heartbeat, model_active, session_id, meta
                FROM agent_status
                WHERE agent_name = ?
                """,
                (agent_id,),
            ).fetchone()
            task_count = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM tasks
                WHERE assigned_to = ?
                  AND status NOT IN ('done', 'cancelled')
                """,
                (agent_id,),
            ).fetchone()["count"]

        result = dict(row)
        result["meta"] = self._load_json(result.get("meta"))
        result["task_count"] = task_count
        result["paused"] = bool(result["meta"].get("paused", False))
        return result

    def delete_agent(self, agent_id: str) -> None:
        self._ensure_agent_exists(agent_id)
        agent_dir = self.agents_dir / agent_id
        with self._connect() as conn:
            conn.execute("DELETE FROM agent_status WHERE agent_name = ?", (agent_id,))
            conn.execute(
                "UPDATE tasks SET assigned_to = NULL, checked_out_by = NULL WHERE assigned_to = ?",
                (agent_id,),
            )
            conn.commit()
        if agent_dir.exists():
            import shutil
            archive_dir = self.apex_home / "archived_agents" / agent_id
            shutil.move(str(agent_dir), str(archive_dir))

    def update_agent_config(self, agent_id: str, config: dict[str, Any]) -> None:
        self._ensure_agent_exists(agent_id)
        agent_dir = self.agents_dir / agent_id
        agent_json_path = agent_dir / "agent.json"
        if not agent_json_path.exists():
            raise ValueError(f"Agent config file not found for '{agent_id}'.")

        existing = json.loads(agent_json_path.read_text())
        for key in ("role", "description", "model", "heartbeat", "heartbeat_description",
                     "capabilities", "can_message", "api_config"):
            if key in config:
                existing[key] = config[key]
        agent_json_path.write_text(json.dumps(existing, indent=2) + "\n")

        if "instructions" in config:
            agent_dir.joinpath("AGENTS.md").write_text(str(config["instructions"]).strip() + "\n")
        if "hard_rules" in config:
            agent_dir.joinpath("constraints", "hard-rules.md").write_text(
                self._normalize_markdown_lines(config["hard_rules"])
            )
        if "soft_preferences" in config:
            agent_dir.joinpath("constraints", "soft-preferences.md").write_text(
                self._normalize_markdown_lines(config["soft_preferences"])
            )
        if "anti_patterns" in config:
            agent_dir.joinpath("constraints", "anti-patterns.md").write_text(
                self._normalize_markdown_lines(config["anti_patterns"])
            )

        with self._connect() as conn:
            model_active = existing.get("model", {}).get("primary", "qwen3.5-apex")
            conn.execute(
                "UPDATE agent_status SET model_active = ? WHERE agent_name = ?",
                (model_active, agent_id),
            )
            conn.commit()

    def create_task(self, task: dict[str, Any]) -> str:
        goal_id = str(task.get("goal_id") or "").strip()
        title = str(task.get("title") or "").strip()
        if not goal_id or not title:
            raise ValueError("Task must include 'goal_id' and 'title'.")

        task_id = str(task.get("id") or f"task-{uuid.uuid4().hex[:12]}")
        project_id = task.get("project_id")
        assigned_to = task.get("assigned_to")

        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM goals WHERE id = ?", (goal_id,)).fetchone() is None:
                raise ValueError(f"Goal '{goal_id}' does not exist.")
            if project_id and conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
                raise ValueError(f"Project '{project_id}' does not exist.")
            if assigned_to:
                self._ensure_agent_exists(str(assigned_to), conn)

            conn.execute(
                """
                INSERT INTO tasks (
                    id, project_id, goal_id, title, description, pipeline_stage,
                    assigned_to, status, priority, review_status, parent_task_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    project_id,
                    goal_id,
                    title,
                    task.get("description", ""),
                    task.get("pipeline_stage"),
                    assigned_to,
                    task.get("status", "backlog"),
                    int(task.get("priority", 2)),
                    task.get("review_status"),
                    task.get("parent_task_id"),
                ),
            )
            conn.commit()

        return task_id

    def assign_task(self, task_id: str, agent_id: str) -> None:
        self._ensure_agent_exists(agent_id)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT checked_out_by FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Task '{task_id}' does not exist.")
            if row["checked_out_by"] and row["checked_out_by"] != agent_id:
                raise ValueError(f"Task '{task_id}' is checked out by '{row['checked_out_by']}'.")

            conn.execute(
                "UPDATE tasks SET assigned_to = ? WHERE id = ?",
                (agent_id, task_id),
            )
            conn.commit()

    def complete_task(self, task_id: str, output: str | None = None) -> None:
        with self._connect() as conn:
            task = conn.execute("SELECT id, checked_out_by FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise ValueError(f"Task '{task_id}' does not exist.")
            conn.execute(
                """
                UPDATE tasks
                SET status = 'done', completed_at = datetime('now'), checked_out_by = NULL
                WHERE id = ?
                """,
                (task_id,),
            )
            if output:
                agent_name = task["checked_out_by"] or "unknown"
                session_id = f"manual-{int(time.time())}"
                conn.execute(
                    """
                    INSERT OR REPLACE INTO agent_sessions (id, agent_name, task_id, context, last_active, status)
                    VALUES (?, ?, ?, ?, datetime('now'), 'complete')
                    """,
                    (session_id, agent_name, task_id, output),
                )
            conn.commit()

    def block_task(self, task_id: str, reason: str) -> None:
        with self._connect() as conn:
            task = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise ValueError(f"Task '{task_id}' does not exist.")
            conn.execute(
                "UPDATE tasks SET status = 'blocked', checked_out_by = NULL WHERE id = ?",
                (task_id,),
            )
            conn.execute(
                """
                INSERT INTO agent_messages (from_agent, to_agent, msg_type, content, task_id, priority)
                VALUES ('system', 'apex', 'escalation', ?, ?, 1)
                """,
                (f"BLOCKED: {reason}", task_id),
            )
            conn.commit()

    def get_task_queue(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        conditions = ["1=1"]
        params: list[Any] = []

        if "status" in filters:
            conditions.append("t.status = ?")
            params.append(filters["status"])
        if "assigned_to" in filters:
            conditions.append("t.assigned_to = ?")
            params.append(filters["assigned_to"])
        if "project_id" in filters:
            conditions.append("t.project_id = ?")
            params.append(filters["project_id"])
        if "pipeline_stage" in filters:
            conditions.append("t.pipeline_stage = ?")
            params.append(filters["pipeline_stage"])
        if "goal_id" in filters:
            conditions.append("t.goal_id = ?")
            params.append(filters["goal_id"])
        if "workspace_id" in filters:
            conditions.append("t.workspace_id = ?")
            params.append(filters["workspace_id"])

        where = " AND ".join(conditions)
        return self._fetch_all(
            f"""
            SELECT t.id, t.project_id, t.goal_id, t.title, t.description,
                   t.pipeline_stage, t.assigned_to, t.checked_out_by,
                   t.status, t.priority, t.review_status,
                   t.created_at, t.completed_at, t.workspace_id,
                   p.name AS project_name, g.name AS goal_name
            FROM tasks t
            LEFT JOIN projects p ON t.project_id = p.id
            LEFT JOIN goals g ON t.goal_id = g.id
            WHERE {where}
            ORDER BY t.priority ASC, t.created_at DESC
            """,
            params,
        )

    def spawn_agent(self, agent_id: str, task_id: str | None = None) -> dict[str, Any]:
        self._ensure_agent_exists(agent_id)
        cmd = [str(self.kernel_dir / "spawn-agent.sh"), agent_id]
        if task_id:
            cmd.append(task_id)

        result = subprocess.run(
            cmd,
            cwd=self.apex_home,
            capture_output=True,
            text=True,
            env=self._subprocess_env(),
            timeout=600,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode != 0 and not result.stdout:
            raise RuntimeError(result.stderr.strip() or f"spawn-agent.sh failed for '{agent_id}'.")

        parsed = self._parse_spawn_output(result.stdout)
        parsed["agent_id"] = agent_id
        parsed["task_id"] = task_id
        parsed["returncode"] = result.returncode
        parsed["stderr"] = result.stderr.strip()
        parsed["raw_output"] = result.stdout
        return parsed

    def submit_for_review(self, task_id: str, stakes: str) -> None:
        stakes = stakes.lower().strip()
        if stakes not in {"low", "medium", "high"}:
            raise ValueError("stakes must be one of: low, medium, high")

        with self._connect() as conn:
            task = conn.execute(
                "SELECT id, assigned_to FROM tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if task is None:
                raise ValueError(f"Task '{task_id}' does not exist.")

            existing = conn.execute(
                "SELECT id FROM reviews WHERE task_id = ? AND verdict IS NULL",
                (task_id,),
            ).fetchone()
            if existing is not None:
                return

            session = conn.execute(
                """
                SELECT id, agent_name
                FROM agent_sessions
                WHERE task_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if session is None:
                raise ValueError(f"No agent session found for task '{task_id}'.")

            conn.execute(
                """
                INSERT INTO reviews (task_id, agent_name, output_ref, stakes)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, session["agent_name"], session["id"], stakes),
            )
            conn.execute(
                "UPDATE tasks SET status = 'review', review_status = 'pending' WHERE id = ?",
                (task_id,),
            )
            conn.commit()

    def run_critic_pipeline(self) -> list[dict[str, Any]]:
        pending_ids = self._fetch_all(
            "SELECT id FROM reviews WHERE verdict IS NULL ORDER BY created_at ASC"
        )
        if not pending_ids:
            return []

        result = subprocess.run(
            ["python3", str(self.kernel_dir / "run_critic.py")],
            cwd=self.apex_home,
            capture_output=True,
            text=True,
            env=self._subprocess_env(),
            timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "run_critic.py failed.")

        review_ids = [row["id"] for row in pending_ids]
        placeholders = ",".join("?" for _ in review_ids)
        query = f"""
            SELECT r.*, t.review_status, t.status AS task_status
            FROM reviews r
            LEFT JOIN tasks t ON t.id = r.task_id
            WHERE r.id IN ({placeholders})
            ORDER BY r.id ASC
        """
        processed = self._fetch_all(query, review_ids)
        for row in processed:
            row["feedback"] = self._load_json(row.get("feedback"), fallback=row.get("feedback"))
        return processed

    def get_approval_queue(self, workspace_id: str | None = None) -> list[dict[str, Any]]:
        extra = "AND r.workspace_id = ?" if workspace_id is not None else ""
        params = (workspace_id,) if workspace_id is not None else ()
        return self._fetch_all(
            f"""
            SELECT
                r.id AS review_id,
                r.task_id,
                r.agent_name,
                r.stakes,
                r.verdict,
                r.feedback,
                r.created_at,
                r.reviewed_at,
                r.workspace_id,
                t.title,
                t.description,
                t.review_status,
                t.status AS task_status
            FROM reviews r
            JOIN tasks t ON t.id = r.task_id
            WHERE t.review_status = 'critic_passed'
            {extra}
            ORDER BY r.created_at ASC
            """,
            params,
        )

    def approve_action(self, review_id: int) -> None:
        with self._connect() as conn:
            review = conn.execute(
                "SELECT id, task_id FROM reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
            if review is None:
                raise ValueError(f"Review '{review_id}' does not exist.")

            conn.execute(
                """
                UPDATE reviews
                SET verdict = 'approved', reviewed_at = datetime('now')
                WHERE id = ?
                """,
                (review_id,),
            )
            conn.execute(
                """
                UPDATE tasks
                SET status = 'done',
                    review_status = 'approved',
                    completed_at = datetime('now'),
                    checked_out_by = NULL
                WHERE id = ?
                """,
                (review["task_id"],),
            )
            conn.commit()

    def reject_action(self, review_id: int, feedback: str) -> None:
        with self._connect() as conn:
            review = conn.execute(
                "SELECT id, task_id, agent_name FROM reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
            if review is None:
                raise ValueError(f"Review '{review_id}' does not exist.")

            conn.execute(
                """
                UPDATE reviews
                SET verdict = 'rejected', feedback = ?, reviewed_at = datetime('now')
                WHERE id = ?
                """,
                (feedback, review_id),
            )
            conn.execute(
                """
                UPDATE tasks
                SET status = 'backlog', review_status = 'rejected', checked_out_by = NULL
                WHERE id = ?
                """,
                (review["task_id"],),
            )
            conn.execute(
                """
                INSERT INTO agent_messages (from_agent, to_agent, msg_type, content, task_id, priority)
                VALUES ('abdul', ?, 'review_feedback', ?, ?, 1)
                """,
                (review["agent_name"], feedback, review["task_id"]),
            )
            conn.commit()

    def send_message(self, from_agent: str, to_agent: str, content: str, msg_type: str) -> None:
        self._ensure_message_party(from_agent)
        self._ensure_message_party(to_agent)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_messages (from_agent, to_agent, msg_type, content)
                VALUES (?, ?, ?, ?)
                """,
                (from_agent, to_agent, msg_type, content),
            )
            conn.commit()

    def get_inbox(self, agent_id: str, workspace_id: str | None = None) -> list[dict[str, Any]]:
        self._ensure_agent_exists(agent_id)
        extra = "AND workspace_id = ?" if workspace_id is not None else ""
        params: list[Any] = [agent_id]
        if workspace_id is not None:
            params.append(workspace_id)
        return self._fetch_all(
            f"""
            SELECT id, created_at, from_agent, to_agent, thread_id, msg_type,
                   priority, content, status, task_id, workspace_id
            FROM agent_messages
            WHERE to_agent = ? AND status = 'pending'
            {extra}
            ORDER BY priority ASC, created_at ASC
            """,
            params,
        )

    def route_user_message(self, text: str) -> dict[str, Any]:
        task_id = f"msg-{int(time.time())}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_messages (from_agent, to_agent, msg_type, content, task_id, priority)
                VALUES ('abdul', 'apex', 'directive', ?, ?, 1)
                """,
                (text, task_id),
            )
            conn.commit()
        return self.spawn_agent("apex")

    def send_to_user(self, text: str, channel: str = "telegram", buttons: list | None = None) -> None:
        if channel == "telegram":
            cmd = ["python3", str(self.kernel_dir.parent / "adapters" / "telegram" / "send_telegram.py"), text]
            if buttons:
                cmd.extend(["--buttons", json.dumps(buttons)])
            subprocess.run(
                cmd, cwd=self.apex_home, capture_output=True, text=True,
                env=self._subprocess_env(), timeout=30,
            )
        else:
            raise ValueError(f"Unsupported channel: {channel}. Available: telegram")

    def get_eval_history(self, agent_id: str, workspace_id: str | None = None) -> list[dict[str, Any]]:
        self._ensure_agent_exists(agent_id)
        extra = "AND workspace_id = ?" if workspace_id is not None else ""
        params: list[Any] = [agent_id]
        if workspace_id is not None:
            params.append(workspace_id)
        return self._fetch_all(
            f"""
            SELECT id, task_id, agent_name, eval_layer, eval_type, dimension,
                   score, max_score, notes, created_at, workspace_id
            FROM evals
            WHERE agent_name = ?
            {extra}
            ORDER BY created_at DESC, id DESC
            """,
            params,
        )

    def route_model(self, agent_id: str, stakes: str = "low") -> str:
        self._ensure_agent_exists(agent_id)
        agent_json_path = self._resolve_agent_config_path(agent_id)
        if agent_json_path is None or not agent_json_path.exists():
            return "qwen3.5-apex"

        config = json.loads(agent_json_path.read_text())

        # Check for stakes-based routing (like Critic)
        stakes_routing = config.get("stakes_routing", {})
        if stakes_routing and stakes in stakes_routing:
            return stakes_routing[stakes]

        # Check for api_review model (used for deep reviews)
        if stakes in ("medium", "high") and config.get("model", {}).get("api_review"):
            return config["model"]["api_review"]

        return config.get("model", {}).get("primary", "qwen3.5-apex")

    def call_model(self, model: str, system_prompt: str, user_prompt: str, temperature: float = 0.3) -> str:
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as sf:
            sf.write(system_prompt)
            sys_path = sf.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as uf:
            uf.write(user_prompt)
            usr_path = uf.name
        try:
            result = subprocess.run(
                ["python3", str(self.kernel_dir / "call_model.py"),
                 model, sys_path, usr_path, str(temperature)],
                cwd=self.apex_home, capture_output=True, text=True,
                env=self._subprocess_env(), timeout=300,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "call_model.py failed.")
            return result.stdout.strip()
        finally:
            os.unlink(sys_path)
            os.unlink(usr_path)

    # ------------------------------------------------------------------ #
    # Tool primitive                                                       #
    # ------------------------------------------------------------------ #

    _VALID_PERMISSION_LEVELS = {"read_only", "draft", "write_with_approval", "full_write"}

    def register_tool(self, config: dict[str, Any]) -> str:
        tool_id = str(config.get("id") or config.get("name") or "").strip().lower().replace(" ", "_")
        if not tool_id:
            raise ValueError("Tool config must include 'id' or 'name'.")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tools (id, name, adapter, auth_method, scopes, read_write, cost_per_call, approval_required)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tool_id,
                    config.get("name", tool_id),
                    config.get("adapter"),
                    config.get("auth_method"),
                    json.dumps(config.get("scopes", [])) if isinstance(config.get("scopes"), list) else config.get("scopes"),
                    config.get("read_write", "read"),
                    float(config.get("cost_per_call", 0)),
                    int(bool(config.get("approval_required", False))),
                ),
            )
            conn.commit()
        return tool_id

    def grant_tool_access(self, agent_id: str, tool_id: str, level: str) -> None:
        self._ensure_agent_exists(agent_id)
        level = level.strip()
        if level not in self._VALID_PERMISSION_LEVELS:
            raise ValueError(f"Invalid permission level '{level}'. Choose from: {sorted(self._VALID_PERMISSION_LEVELS)}")
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM tools WHERE id = ?", (tool_id,)).fetchone() is None:
                raise ValueError(f"Tool '{tool_id}' does not exist.")
            conn.execute(
                """
                INSERT INTO tool_grants (agent_id, tool_id, permission_level)
                VALUES (?, ?, ?)
                ON CONFLICT(agent_id, tool_id) DO UPDATE SET permission_level = excluded.permission_level
                """,
                (agent_id, tool_id, level),
            )
            conn.commit()

    def revoke_tool_access(self, agent_id: str, tool_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM tool_grants WHERE agent_id = ? AND tool_id = ?",
                (agent_id, tool_id),
            )
            conn.commit()

    def get_agent_tools(self, agent_id: str) -> list[dict[str, Any]]:
        self._ensure_agent_exists(agent_id)
        return self._fetch_all(
            """
            SELECT tg.id AS grant_id, tg.permission_level, tg.created_at AS granted_at,
                   t.id AS tool_id, t.name, t.adapter, t.auth_method, t.scopes,
                   t.read_write, t.cost_per_call, t.approval_required
            FROM tool_grants tg
            JOIN tools t ON t.id = tg.tool_id
            WHERE tg.agent_id = ?
            ORDER BY t.name
            """,
            (agent_id,),
        )

    def invoke_tool(self, agent_id: str, tool_id: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_agent_exists(agent_id)
        with self._connect() as conn:
            grant = conn.execute(
                "SELECT permission_level FROM tool_grants WHERE agent_id = ? AND tool_id = ?",
                (agent_id, tool_id),
            ).fetchone()
            if grant is None:
                raise PermissionError(f"Agent '{agent_id}' has no access to tool '{tool_id}'.")

            tool = conn.execute("SELECT * FROM tools WHERE id = ?", (tool_id,)).fetchone()
            if tool is None:
                raise ValueError(f"Tool '{tool_id}' does not exist.")

        level = grant["permission_level"]
        tool_rw = tool["read_write"]
        approval_required = bool(tool["approval_required"])
        cost = float(tool["cost_per_call"] or 0)

        # Write-capable operations require at least write_with_approval
        if tool_rw == "write" and level == "read_only":
            raise PermissionError(
                f"Agent '{agent_id}' has read_only access to '{tool_id}' but tool requires write."
            )
        # Approval-required tools need write_with_approval or full_write
        if approval_required and level in ("read_only", "draft"):
            raise PermissionError(
                f"Tool '{tool_id}' requires approval. "
                f"Agent '{agent_id}' needs write_with_approval or full_write level (has: {level})."
            )

        # Record cost against budget if tool has a cost
        if cost > 0:
            try:
                status = self.check_budget(agent_id, "tool_cost", cost)
                if status == "denied":
                    raise PermissionError(f"Agent '{agent_id}' is over budget for tool invocations.")
                self.record_spend(agent_id, "tool_cost", cost, f"invoke:{tool_id}")
            except ValueError:
                pass  # No budget set — allow invocation without tracking

        return {
            "tool_id": tool_id,
            "agent_id": agent_id,
            "permission_level": level,
            "params": params or {},
            "status": "authorized",
        }

    # ------------------------------------------------------------------ #
    # Permission primitive                                                 #
    # ------------------------------------------------------------------ #

    def set_permission(
        self,
        agent_id: str,
        resource: str,
        level: str,
        max_spend_per_day: float | None = None,
        requires_approval: bool = False,
    ) -> None:
        self._ensure_agent_exists(agent_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO permissions (agent_id, resource, level, max_spend_per_day, requires_approval)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, resource) DO UPDATE SET
                    level = excluded.level,
                    max_spend_per_day = excluded.max_spend_per_day,
                    requires_approval = excluded.requires_approval
                """,
                (agent_id, resource, level, max_spend_per_day, int(requires_approval)),
            )
            conn.commit()

    def check_permission(self, agent_id: str, resource: str, action: str = "read") -> str:
        """Return 'allowed', 'denied', or 'needs_approval'."""
        row = self._fetch_all(
            "SELECT level, requires_approval FROM permissions WHERE agent_id = ? AND resource = ?",
            (agent_id, resource),
        )
        if not row:
            return "denied"

        perm = row[0]
        level = perm["level"]
        requires_approval = bool(perm["requires_approval"])

        # Write actions require at least draft level
        if action == "write" and level == "read_only":
            return "denied"
        if requires_approval:
            return "needs_approval"
        return "allowed"

    def get_agent_permissions(self, agent_id: str) -> list[dict[str, Any]]:
        self._ensure_agent_exists(agent_id)
        return self._fetch_all(
            """
            SELECT id, agent_id, resource, level, max_spend_per_day,
                   requires_approval, created_at, workspace_id
            FROM permissions
            WHERE agent_id = ?
            ORDER BY resource
            """,
            (agent_id,),
        )

    # ------------------------------------------------------------------ #
    # Budget primitive                                                     #
    # ------------------------------------------------------------------ #

    def set_budget(
        self,
        agent_id: str,
        budget_type: str,
        limit_amount: float,
        period: str = "daily",
        alert_threshold: float = 0.8,
    ) -> None:
        self._ensure_agent_exists(agent_id)
        if limit_amount <= 0:
            raise ValueError("limit_amount must be positive.")
        if not (0 < alert_threshold <= 1):
            raise ValueError("alert_threshold must be between 0 and 1 (e.g. 0.8 = 80%).")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO budgets (agent_id, budget_type, limit_amount, period, alert_threshold)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, budget_type) DO UPDATE SET
                    limit_amount = excluded.limit_amount,
                    period = excluded.period,
                    alert_threshold = excluded.alert_threshold
                """,
                (agent_id, budget_type, float(limit_amount), period, float(alert_threshold)),
            )
            conn.commit()

    def record_spend(self, agent_id: str, budget_type: str, amount: float, description: str = "") -> None:
        with self._connect() as conn:
            budget = conn.execute(
                "SELECT id, limit_amount, spent_amount FROM budgets WHERE agent_id = ? AND budget_type = ?",
                (agent_id, budget_type),
            ).fetchone()
            if budget is None:
                raise ValueError(f"No budget found for agent '{agent_id}', type '{budget_type}'.")

            new_spent = float(budget["spent_amount"]) + float(amount)
            if new_spent > float(budget["limit_amount"]):
                raise PermissionError(
                    f"Spend of {amount} would exceed budget limit {budget['limit_amount']} "
                    f"for agent '{agent_id}' type '{budget_type}' "
                    f"(current: {budget['spent_amount']})."
                )

            conn.execute(
                "UPDATE budgets SET spent_amount = ? WHERE id = ?",
                (new_spent, budget["id"]),
            )
            conn.execute(
                "INSERT INTO spend_log (agent_id, budget_id, amount, description) VALUES (?, ?, ?, ?)",
                (agent_id, budget["id"], float(amount), description),
            )
            conn.commit()

    def get_budget_status(self, agent_id: str) -> list[dict[str, Any]]:
        self._ensure_agent_exists(agent_id)
        budgets = self._fetch_all(
            """
            SELECT id, agent_id, budget_type, limit_amount, spent_amount,
                   period, alert_threshold, created_at, workspace_id
            FROM budgets
            WHERE agent_id = ?
            ORDER BY budget_type
            """,
            (agent_id,),
        )
        for b in budgets:
            limit = float(b["limit_amount"])
            spent = float(b["spent_amount"])
            threshold = float(b["alert_threshold"])
            remaining = limit - spent
            b["remaining"] = round(remaining, 6)
            b["utilization"] = round(spent / limit, 4) if limit > 0 else 0
            b["status"] = (
                "over_limit" if spent >= limit
                else "warning" if spent >= threshold * limit
                else "ok"
            )
        return budgets

    def check_budget(self, agent_id: str, budget_type: str, amount: float) -> str:
        """Return 'allowed', 'warning', or 'denied'."""
        rows = self._fetch_all(
            "SELECT limit_amount, spent_amount, alert_threshold FROM budgets WHERE agent_id = ? AND budget_type = ?",
            (agent_id, budget_type),
        )
        if not rows:
            raise ValueError(f"No budget found for agent '{agent_id}', type '{budget_type}'.")

        b = rows[0]
        limit = float(b["limit_amount"])
        spent = float(b["spent_amount"])
        threshold = float(b["alert_threshold"])
        projected = spent + float(amount)

        if projected > limit:
            return "denied"
        if projected >= threshold * limit:
            return "warning"
        return "allowed"

    # ------------------------------------------------------------------ #
    # Workspace primitive                                                  #
    # ------------------------------------------------------------------ #

    def create_workspace(self, template_id: str, name: str | None = None) -> str:
        """Create a new workspace for the given template. Returns the workspace_id."""
        workspace_id = f"ws-{uuid.uuid4().hex[:8]}"
        name = name or f"{template_id}-{workspace_id}"
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO workspaces (id, template_id, name, status) VALUES (?, ?, ?, 'active')",
                (workspace_id, template_id, name),
            )
            conn.commit()
        return workspace_id

    def list_workspaces(self) -> list[dict[str, Any]]:
        """Return all workspaces with agent counts."""
        workspaces = self._fetch_all(
            "SELECT id, template_id, name, status, created_at FROM workspaces ORDER BY created_at DESC"
        )
        with self._connect() as conn:
            for ws in workspaces:
                row = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM agent_status WHERE workspace_id = ?",
                    (ws["id"],),
                ).fetchone()
                ws["agent_count"] = row["cnt"] if row else 0
        return workspaces

    def get_workspace(self, workspace_id: str) -> dict[str, Any]:
        """Return a workspace by id, including its agents."""
        rows = self._fetch_all(
            "SELECT id, template_id, name, status, created_at FROM workspaces WHERE id = ?",
            (workspace_id,),
        )
        if not rows:
            raise ValueError(f"Workspace '{workspace_id}' does not exist.")
        ws = rows[0]
        ws["agents"] = self._fetch_all(
            "SELECT agent_name, status, model_active, last_heartbeat FROM agent_status WHERE workspace_id = ?",
            (workspace_id,),
        )
        ws["agent_count"] = len(ws["agents"])
        return ws

    def delete_workspace(self, workspace_id: str) -> None:
        """Mark a workspace inactive and remove its agents from agent_status."""
        rows = self._fetch_all(
            "SELECT id FROM workspaces WHERE id = ?", (workspace_id,)
        )
        if not rows:
            raise ValueError(f"Workspace '{workspace_id}' does not exist.")
        with self._connect() as conn:
            conn.execute(
                "UPDATE workspaces SET status = 'deleted' WHERE id = ?", (workspace_id,)
            )
            conn.execute(
                "DELETE FROM agent_status WHERE workspace_id = ?", (workspace_id,)
            )
            conn.execute(
                "DELETE FROM permissions WHERE workspace_id = ?", (workspace_id,)
            )
            conn.execute(
                "DELETE FROM budgets WHERE workspace_id = ?", (workspace_id,)
            )
            conn.commit()

    # ------------------------------------------------------------------ #
    # Template primitive                                                   #
    # ------------------------------------------------------------------ #

    def list_templates(self) -> list[dict[str, Any]]:
        """Return a summary list of all available templates."""
        templates_dir = self.apex_home / "templates"
        result = []
        if not templates_dir.exists():
            return result
        for entry in sorted(templates_dir.iterdir()):
            manifest_path = entry / "template.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text())
                result.append({
                    "id": entry.name,
                    "name": manifest.get("name", entry.name),
                    "description": manifest.get("description", ""),
                    "category": manifest.get("category", ""),
                    "version": manifest.get("version", ""),
                    "agent_count": len(manifest.get("agents", [])),
                    "pipeline": manifest.get("pipeline", []),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return result

    def get_template(self, template_id: str) -> dict[str, Any]:
        """Return the full manifest for a given template_id."""
        manifest_path = self.apex_home / "templates" / template_id / "template.json"
        if not manifest_path.exists():
            raise FileNotFoundError(f"Template '{template_id}' not found at {manifest_path}")
        return json.loads(manifest_path.read_text())

    def launch_template(
        self,
        template_id: str,
        overrides: dict[str, Any] | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Instantiate a template inside a workspace.

        Each agent is namespaced as {workspace_id}-{agent_name} to prevent
        collisions when the same template is launched multiple times or when
        two templates share agent role names (e.g., both have a 'scout').

        Backward-compatible: if workspace_id is explicitly passed as the string
        "global", agents are registered without namespacing (legacy behaviour).

        Returns a summary dict with keys:
          template_name, template_id, workspace_id, agents_created,
          permissions_applied, budgets_applied
        """
        manifest = self.get_template(template_id)

        missing = [f for f in ("name", "agents", "pipeline") if f not in manifest]
        if missing:
            raise ValueError(f"Template '{template_id}' missing required fields: {missing}")

        overrides = overrides or {}
        global_mode = workspace_id == "global"

        # Auto-create workspace unless caller passed one or requested global mode
        if workspace_id is None:
            workspace_name = overrides.get("workspace_name") or f"{template_id}-launch"
            workspace_id = self.create_workspace(template_id, name=workspace_name)
        elif not global_mode:
            # Verify the workspace exists
            self.get_workspace(workspace_id)

        agent_overrides: dict[str, Any] = overrides.get("agents", {})
        template_dir = self.apex_home / "templates" / template_id
        agents_dir = template_dir / "agents"

        agents_created: list[str] = []
        permissions_applied = 0
        budgets_applied = 0

        for agent_cfg in manifest.get("agents", []):
            template_agent_name = str(agent_cfg.get("name") or "").strip()
            if not template_agent_name:
                continue

            # Namespace agent unless in global (legacy) mode
            agent_id = (
                template_agent_name
                if global_mode
                else f"{workspace_id}-{template_agent_name}"
            )

            # Merge per-agent overrides (keyed by template agent name)
            if template_agent_name in agent_overrides:
                agent_cfg = {**agent_cfg, **agent_overrides[template_agent_name]}

            # Agent files always live in the template directory (shared, read-only)
            agent_dir = agents_dir / template_agent_name
            agent_dir.joinpath("constraints").mkdir(parents=True, exist_ok=True)
            agent_dir.joinpath("workspace").mkdir(parents=True, exist_ok=True)

            agent_json = {
                "name": template_agent_name,
                "role": agent_cfg.get("role", "custom"),
                "description": agent_cfg.get("description", ""),
                "model": agent_cfg.get("model", {"primary": "qwen3.5-apex", "fallback": "claude-sonnet"}),
                "heartbeat": agent_cfg.get("heartbeat"),
                "heartbeat_description": agent_cfg.get("heartbeat_description"),
                "capabilities": agent_cfg.get("capabilities", []),
                "can_message": agent_cfg.get("can_message", []),
                "api_config": agent_cfg.get("api_config", {"think": False, "num_ctx": 4096, "temperature": 0.3}),
            }

            agent_json_path = agent_dir / "agent.json"
            if not agent_json_path.exists():
                agent_json_path.write_text(json.dumps(agent_json, indent=2) + "\n")
            for fname in ("AGENTS.md",):
                p = agent_dir / fname
                if not p.exists():
                    p.write_text("")
            for fname in ("hard-rules.md", "soft-preferences.md", "anti-patterns.md"):
                p = agent_dir / "constraints" / fname
                if not p.exists():
                    p.write_text("")
            scratch = agent_dir / "workspace" / "scratchpad.md"
            if not scratch.exists():
                scratch.write_text("")

            meta = {
                "paused": False,
                "config_path": str(agent_json_path),
                "template_id": template_id,
                "template_agent_name": template_agent_name,
                "workspace_id": workspace_id if not global_mode else None,
            }
            ws_col = None if global_mode else workspace_id

            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO agent_status
                        (agent_name, status, model_active, meta, workspace_id)
                    VALUES (?, 'idle', ?, ?, ?)
                    """,
                    (
                        agent_id,
                        agent_json["model"].get("primary", "qwen3.5-apex"),
                        json.dumps(meta),
                        ws_col,
                    ),
                )
                conn.commit()
                if cur.rowcount > 0:
                    agents_created.append(agent_id)

            # Apply default_permissions
            default_perms = manifest.get("default_permissions", {})
            if isinstance(default_perms, dict):
                for category, resources in default_perms.items():
                    if isinstance(resources, dict):
                        for resource_key, perm_value in resources.items():
                            resource = f"{category}.{resource_key}"
                            level, requires_approval = self._map_template_permission(perm_value)
                            self._set_permission_ws(
                                agent_id, resource, level,
                                requires_approval=requires_approval,
                                workspace_id=ws_col,
                            )
                            permissions_applied += 1

            # Apply default_budgets
            for budget_type, budget_cfg in manifest.get("default_budgets", {}).items():
                if isinstance(budget_cfg, dict):
                    self._set_budget_ws(
                        agent_id,
                        budget_type,
                        float(budget_cfg.get("limit", 100.0)),
                        period=budget_cfg.get("period", "daily"),
                        alert_threshold=float(budget_cfg.get("alert_threshold", 0.8)),
                        workspace_id=ws_col,
                    )
                    budgets_applied += 1

        # Auto-grant tool access for integrations listed in the manifest
        _SEARCH_GRANT_ROLES = {"scout", "analyst"}
        all_integrations = (
            set(manifest.get("integrations", []))
            | set(manifest.get("optional_integrations", []))
        )
        tools_granted: list[str] = []
        if "web_search" in all_integrations:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO tools
                        (id, name, adapter, auth_method, scopes, read_write, cost_per_call, approval_required)
                    VALUES ('web_search', 'Web Search', 'adapters.tools.web_search.search',
                            'none', '["search","research","evidence"]', 'read_only', 0, 0)
                    """,
                )
                for agent_cfg in manifest.get("agents", []):
                    tmpl_name = str(agent_cfg.get("name") or "").strip()
                    if tmpl_name not in _SEARCH_GRANT_ROLES:
                        continue
                    agent_id = tmpl_name if global_mode else f"{workspace_id}-{tmpl_name}"
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO tool_grants (agent_id, tool_id, permission_level)
                        VALUES (?, 'web_search', 'read_only')
                        """,
                        (agent_id,),
                    )
                    tools_granted.append(agent_id)
                conn.commit()

        return {
            "template_name": manifest["name"],
            "template_id": template_id,
            "workspace_id": workspace_id,
            "agents_created": agents_created,
            "permissions_applied": permissions_applied,
            "budgets_applied": budgets_applied,
            "tools_granted": tools_granted,
        }

    def _set_permission_ws(
        self,
        agent_id: str,
        resource: str,
        level: str,
        max_spend_per_day: float | None = None,
        requires_approval: bool = False,
        workspace_id: str | None = None,
    ) -> None:
        """Internal upsert that propagates workspace_id to the permissions row."""
        self._ensure_agent_exists(agent_id)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO permissions
                    (agent_id, resource, level, max_spend_per_day, requires_approval, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, resource) DO UPDATE SET
                    level = excluded.level,
                    max_spend_per_day = excluded.max_spend_per_day,
                    requires_approval = excluded.requires_approval,
                    workspace_id = excluded.workspace_id
                """,
                (agent_id, resource, level, max_spend_per_day, int(requires_approval), workspace_id),
            )
            conn.commit()

    def _set_budget_ws(
        self,
        agent_id: str,
        budget_type: str,
        limit_amount: float,
        period: str = "daily",
        alert_threshold: float = 0.8,
        workspace_id: str | None = None,
    ) -> None:
        """Internal upsert that propagates workspace_id to the budgets row."""
        self._ensure_agent_exists(agent_id)
        if limit_amount <= 0:
            raise ValueError("limit_amount must be positive.")
        if not (0 < alert_threshold <= 1):
            raise ValueError("alert_threshold must be between 0 and 1.")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO budgets
                    (agent_id, budget_type, limit_amount, period, alert_threshold, workspace_id)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, budget_type) DO UPDATE SET
                    limit_amount = excluded.limit_amount,
                    period = excluded.period,
                    alert_threshold = excluded.alert_threshold,
                    workspace_id = excluded.workspace_id
                """,
                (agent_id, budget_type, float(limit_amount), period, float(alert_threshold), workspace_id),
            )
            conn.commit()

    def _map_template_permission(self, value: str) -> tuple[str, bool]:
        """Map a human-readable template permission descriptor to (level, requires_approval)."""
        mapping: dict[str, tuple[str, bool]] = {
            "allowed_with_task_assignment": ("full_write", False),
            "approval_required": ("write_with_approval", True),
            "human_approval_required": ("draft", True),
            "allowlisted": ("read_only", False),
            "disabled_in_phase_1_5": ("read_only", True),
            "read_only": ("read_only", False),
            "full_write": ("full_write", False),
            "write_with_approval": ("write_with_approval", False),
            "draft": ("draft", False),
        }
        return mapping.get(str(value).strip(), ("read_only", False))

    def _migrate(self) -> None:
        """Apply incremental schema migrations to the live database."""
        _WORKSPACE_TABLE = """
            CREATE TABLE IF NOT EXISTS workspaces (
                id TEXT PRIMARY KEY,
                template_id TEXT NOT NULL,
                name TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                created_at TEXT DEFAULT (datetime('now'))
            )
        """
        _WORKSPACE_IDX = (
            "CREATE INDEX IF NOT EXISTS idx_workspaces_template ON workspaces(template_id)"
        )
        _NEW_COLS: dict[str, list[str]] = {
            "agent_status": ["workspace_id TEXT"],
            "tasks": ["workspace_id TEXT"],
            "agent_messages": ["workspace_id TEXT"],
            "reviews": ["workspace_id TEXT"],
            "evals": ["workspace_id TEXT"],
            "permissions": ["workspace_id TEXT"],
            "budgets": ["workspace_id TEXT"],
            "tool_grants": ["workspace_id TEXT"],
        }
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(_WORKSPACE_TABLE)
            conn.execute(_WORKSPACE_IDX)
            for table, columns in _NEW_COLS.items():
                for col_def in columns:
                    col_name = col_def.split()[0]
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_def}")
                    except sqlite3.OperationalError:
                        pass  # Column already exists
            conn.commit()
        finally:
            conn.close()

    def _resolve_agent_config_path(self, agent_id: str) -> Path | None:
        """
        Return the Path to agent.json for agent_id.

        For workspace-namespaced agents (e.g. ws-abc123-scout), the agent files
        live in the template directory, not under the namespaced name. The actual
        path is stored in agent_status.meta['config_path'].

        Falls back to self.agents_dir / agent_id / agent.json for global agents.
        """
        # Fast path: direct file exists (global, un-namespaced agent)
        direct = self.agents_dir / agent_id / "agent.json"
        if direct.exists():
            return direct

        # Look up meta for workspace-scoped agents
        with self._connect() as conn:
            row = conn.execute(
                "SELECT meta FROM agent_status WHERE agent_name = ?", (agent_id,)
            ).fetchone()
        if not row:
            return None
        meta = self._load_json(row["meta"])
        config_path = meta.get("config_path")
        if config_path:
            return Path(config_path)
        return None

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _fetch_all(self, sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(sql, params or ()).fetchall()
        return [dict(row) for row in rows]

    def _ensure_agent_exists(self, agent_id: str, conn: sqlite3.Connection | None = None) -> None:
        handle = conn or self._connect()
        close_conn = conn is None
        try:
            row = handle.execute(
                "SELECT 1 FROM agent_status WHERE agent_name = ?",
                (agent_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Agent '{agent_id}' does not exist.")
        finally:
            if close_conn:
                handle.close()

    def _ensure_message_party(self, party: str) -> None:
        if party == "abdul":
            return
        self._ensure_agent_exists(party)

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["APEX_HOME"] = str(self.apex_home)
        # Ensure PYTHONPATH includes apex_home so kernel.* imports work in subprocesses
        existing_pp = env.get("PYTHONPATH", "")
        apex_str = str(self.apex_home)
        if apex_str not in existing_pp:
            env["PYTHONPATH"] = apex_str + (":" + existing_pp if existing_pp else "")
        # Load any missing API keys from .env so subprocesses always have them
        _env_file = self.apex_home / ".env"
        if _env_file.exists():
            for _line in _env_file.read_text().splitlines():
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _v = _line.split("=", 1)
                _k = _k.strip()
                if _k and _k not in env:
                    env[_k] = _v.strip()
        return env

    def _parse_spawn_output(self, output: str) -> dict[str, Any]:
        parsed: dict[str, Any] = {
            "actions_taken": "",
            "observations": "",
            "proposed_output": None,
            "messages": [],
            "scratchpad_update": "",
            "status": {"state": "unknown", "reason": ""},
        }

        section: str | None = None
        proposed_lines: list[str] = []
        observation_lines: list[str] = []
        actions_lines: list[str] = []
        scratchpad_lines: list[str] = []

        for raw_line in output.splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()

            if stripped.startswith("ACTIONS:"):
                section = "actions"
                actions_lines = [stripped.partition(":")[2].strip()]
                continue
            if stripped.startswith("OBSERVATIONS:"):
                section = "observations"
                observation_lines = [stripped.partition(":")[2].strip()]
                continue
            if stripped.startswith("PROPOSED OUTPUT:"):
                section = "proposed_output"
                proposed_lines = [stripped.partition(":")[2].strip()]
                continue
            if stripped.startswith("MESSAGES: none"):
                section = None
                parsed["messages"] = []
                continue
            if stripped.startswith("MESSAGES"):
                section = "messages"
                continue
            if stripped.startswith("SCRATCHPAD:"):
                section = "scratchpad"
                scratchpad_lines = [stripped.partition(":")[2].strip()]
                continue
            if stripped.startswith("STATUS:"):
                parsed["status"] = normalize_status(stripped.partition(":")[2].strip())
                section = None
                continue

            if section == "messages":
                message = self._parse_message_line(stripped)
                if message is not None:
                    parsed["messages"].append(message)
                continue
            if section == "proposed_output" and stripped:
                proposed_lines.append(stripped)
                continue
            if section == "observations" and stripped:
                observation_lines.append(stripped)
                continue
            if section == "actions" and stripped:
                actions_lines.append(stripped)
                continue
            if section == "scratchpad" and stripped:
                scratchpad_lines.append(stripped)

        parsed["actions_taken"] = "\n".join(filter(None, actions_lines)).strip()
        parsed["observations"] = "\n".join(filter(None, observation_lines)).strip()
        parsed["proposed_output"] = self._coerce_value("\n".join(filter(None, proposed_lines)).strip())
        parsed["scratchpad_update"] = "\n".join(filter(None, scratchpad_lines)).strip()
        return parsed

    def _parse_message_line(self, line: str) -> dict[str, Any] | None:
        if not line.startswith("→"):
            return None
        header, _, content = line.partition("]")
        target_part, _, type_part = header.partition(":")
        to_agent = target_part.replace("→", "").strip()
        msg_type = type_part.replace("[", "").strip() or "request"
        return {"to": to_agent, "type": msg_type, "content": content.strip()}

    def _coerce_value(self, value: str) -> Any:
        if not value or value == "None":
            return None
        for parser in (json.loads, ast.literal_eval):
            try:
                return parser(value)
            except (ValueError, SyntaxError, json.JSONDecodeError):
                continue
        return value

    def _load_json(self, value: Any, fallback: Any | None = None) -> Any:
        if value in (None, ""):
            return {} if fallback is None else fallback
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {} if fallback is None else fallback

    def _normalize_markdown_lines(self, value: Any) -> str:
        if isinstance(value, str):
            text = value.strip()
            return f"{text}\n" if text else ""
        if isinstance(value, list):
            lines = [f"- {str(item).strip()}" for item in value if str(item).strip()]
            return ("\n".join(lines) + "\n") if lines else ""
        return ""
