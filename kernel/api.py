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

        where = " AND ".join(conditions)
        return self._fetch_all(
            f"""
            SELECT t.id, t.project_id, t.goal_id, t.title, t.description,
                   t.pipeline_stage, t.assigned_to, t.checked_out_by,
                   t.status, t.priority, t.review_status,
                   t.created_at, t.completed_at,
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

    def get_approval_queue(self) -> list[dict[str, Any]]:
        return self._fetch_all(
            """
            SELECT
                r.id AS review_id,
                r.task_id,
                r.agent_name,
                r.stakes,
                r.verdict,
                r.feedback,
                r.created_at,
                r.reviewed_at,
                t.title,
                t.description,
                t.review_status,
                t.status AS task_status
            FROM reviews r
            JOIN tasks t ON t.id = r.task_id
            WHERE t.review_status = 'critic_passed'
            ORDER BY r.created_at ASC
            """
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

    def get_inbox(self, agent_id: str) -> list[dict[str, Any]]:
        self._ensure_agent_exists(agent_id)
        return self._fetch_all(
            """
            SELECT id, created_at, from_agent, to_agent, thread_id, msg_type, priority, content, status, task_id
            FROM agent_messages
            WHERE to_agent = ? AND status = 'pending'
            ORDER BY priority ASC, created_at ASC
            """,
            (agent_id,),
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

    def get_eval_history(self, agent_id: str) -> list[dict[str, Any]]:
        self._ensure_agent_exists(agent_id)
        return self._fetch_all(
            """
            SELECT id, task_id, agent_name, eval_layer, eval_type, dimension, score, max_score, notes, created_at
            FROM evals
            WHERE agent_name = ?
            ORDER BY created_at DESC, id DESC
            """,
            (agent_id,),
        )

    def route_model(self, agent_id: str, stakes: str = "low") -> str:
        self._ensure_agent_exists(agent_id)
        agent_json_path = self.agents_dir / agent_id / "agent.json"
        if not agent_json_path.exists():
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
