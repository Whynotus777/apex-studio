from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from kernel import call_model, parse_response, spawn_context
from kernel.api import ApexKernel
from kernel.memory_loader import load_agent_memory, save_agent_memory


class AgentRunner:
    """Python port of the core spawn-agent.sh orchestration."""

    def __init__(self, apex_home: str | Path | None = None, db_path: str | Path | None = None) -> None:
        self.apex_home = Path(apex_home or os.environ.get("APEX_HOME") or Path(__file__).resolve().parents[1]).resolve()
        self.db_path = Path(db_path or self.apex_home / "db" / "apex_state.db").resolve()
        self.kernel = ApexKernel(apex_home=self.apex_home, db_path=self.db_path)
        self.workspace_dir = self.apex_home / "templates" / "startup-chief-of-staff" / "workspace"

    def run(self, agent_name: str, task_id: str | None = None) -> dict[str, Any]:
        self._load_dotenv()
        agent_dir = self._resolve_agent_dir(agent_name)
        if not agent_dir.exists():
            raise FileNotFoundError(f"Agent '{agent_name}' not found (looked in {agent_dir})")

        session_id = f"sess-{agent_name}-{int(time.time())}"
        self._update_agent_status_active(agent_name, session_id)

        try:
            agent_config = self._load_agent_config(agent_dir)
            system_prompt = self._build_system_prompt(agent_dir, agent_config)
            user_prompt = self._build_user_prompt(agent_name, task_id)
            response = self._call_model_with_fallbacks(
                agent_config,
                system_prompt,
                user_prompt,
            )
            parsed = parse_response.parse_response(response)
            self._save_memory(agent_name, session_id, task_id, response, parsed)
            self._process_messages(agent_name, task_id, parsed)
            if task_id:
                self._process_task_status(agent_name, task_id, session_id, parsed)
            return {
                "agent_name": agent_name,
                "task_id": task_id or "",
                "session_id": session_id,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response": response,
                "parsed": parsed,
                "parse_method": parsed.get("_parse_method", "unknown"),
            }
        finally:
            self._update_agent_status_idle(agent_name)

    def _load_dotenv(self) -> None:
        env_path = self.apex_home / ".env"
        if not env_path.exists():
            return
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))

    def _resolve_agent_dir(self, agent_name: str) -> Path:
        config_path = self.kernel._resolve_agent_config_path(agent_name)
        if config_path is not None and config_path.exists():
            return config_path.parent
        return self.apex_home / "templates" / "startup-chief-of-staff" / "agents" / agent_name

    def _load_agent_config(self, agent_dir: Path) -> dict[str, Any]:
        return json.loads(agent_dir.joinpath("agent.json").read_text(encoding="utf-8"))

    def _build_system_prompt(self, agent_dir: Path, agent_config: dict[str, Any]) -> str:
        hard_rules_path = agent_dir / "constraints" / "hard-rules.md"
        hard_rules = hard_rules_path.read_text(encoding="utf-8") if hard_rules_path.exists() else ""
        role = agent_config.get("role", "")
        description = agent_config.get("description", "")
        return (
            f"You are a {role} agent.\n"
            f"Job: {description}\n\n"
            f"Hard rules:\n"
            f"{hard_rules}\n\n"
            "Search Evidence grounding: If Search Evidence is present in the user message, "
            "you may ONLY cite URLs and facts that appear in the Search Evidence section. "
            "If Search Evidence is absent or empty, do not invent sources and state that no evidence was retrieved.\n\n"
            "Respond with ONLY a valid JSON object. No text before or after the JSON. Use this exact schema:\n"
            "{\n"
            '  "actions_taken": "what you actually did (not what you would do)",\n'
            '  "observations": "what you noticed about your context and task",\n'
            '  "proposed_output": "your deliverable, clearly labeled as proposed if not executed",\n'
            '  "messages": [\n'
            '    {"to": "agent_name", "type": "request|alert|escalation", "content": "message"}\n'
            "  ],\n"
            '  "scratchpad_update": "key facts to remember",\n'
            '  "status": "done|blocked:reason|needs_review:low|needs_review:medium|needs_review:high"\n'
            "}\n\n"
            "Valid message targets: apex, scout, analyst, builder, critic. No other targets allowed.\n"
            'If no messages needed, use an empty array: "messages": []\n'
        )

    def _build_user_prompt(self, agent_name: str, task_id: str | None) -> str:
        memory = load_agent_memory(agent_name, apex_home=self.apex_home)
        parts: list[str] = []

        session_context = str(memory.get("session_context") or "")
        if session_context:
            parts.extend(["Latest session context:", session_context, ""])

        working_memory = str(memory.get("working_memory") or "")
        if working_memory:
            parts.extend(["Working memory:", working_memory, ""])

        durable_memory = str(memory.get("durable_memory") or "")
        if durable_memory:
            parts.extend(["Durable memory:", durable_memory, ""])

        inbox = self._get_inbox(agent_name)
        if inbox:
            parts.extend(["Inbox:", inbox, ""])
        self._mark_inbox_read(agent_name)

        if task_id:
            task_info = self._get_task_info(task_id)
            if task_info:
                self._checkout_task(agent_name, task_id)
                parts.extend([f"Task: {task_id}", task_info])
        else:
            parts.append("No specific task. Run your heartbeat responsibilities.")

        if task_id:
            spawn_ctx = self._build_spawn_context(agent_name, task_id)
            if spawn_ctx:
                parts.extend(["", spawn_ctx])

        return "\n".join(parts)

    def _call_model_with_fallbacks(
        self,
        agent_config: dict[str, Any],
        system_prompt: str,
        user_prompt: str,
    ) -> str:
        model_config = agent_config.get("model", {})
        api_config = agent_config.get("api_config", {})
        primary = model_config.get("primary", "")
        fallback = model_config.get("fallback", "")
        temperature = float(api_config.get("temperature", 0.3))

        response = self._call_model(primary, system_prompt, user_prompt, temperature)
        if not response and fallback:
            if fallback.startswith("claude"):
                if os.environ.get("ANTHROPIC_API_KEY"):
                    response = self._call_model(fallback, system_prompt, user_prompt, temperature)
            else:
                response = self._call_model(fallback, system_prompt, user_prompt, temperature)

        if not response:
            response = self._call_model("qwen3.5-apex", system_prompt, user_prompt, 0.3)

        if response:
            return response

        return (
            '{"actions_taken":"none","observations":"All model calls failed",'
            '"proposed_output":"none","messages":[],"scratchpad_update":"Model call failure",'
            '"status":"blocked:model_failure"}'
        )

    def _call_model(self, model: str, system_prompt: str, user_prompt: str, temperature: float) -> str:
        if not model:
            return ""
        if model.startswith("claude-opus"):
            return call_model.call_claude("claude-opus-4-20250514", system_prompt, user_prompt, temperature)
        if model.startswith("claude-sonnet"):
            return call_model.call_claude("claude-sonnet-4-20250514", system_prompt, user_prompt, temperature)
        if model.startswith("gemini"):
            return call_model.call_gemini(model, system_prompt, user_prompt, temperature)
        return call_model.call_ollama(model, system_prompt, user_prompt, temperature)

    def _save_memory(
        self,
        agent_name: str,
        session_id: str,
        task_id: str | None,
        response: str,
        parsed: dict[str, Any],
    ) -> None:
        scratchpad = str(parsed.get("scratchpad_update", ""))
        with self._temporary_env(
            MEMORY_SESSION_CONTEXT=response,
            APEX_TASK_ID=task_id or "",
            APEX_HOME=str(self.apex_home),
        ):
            save_agent_memory(agent_name, session_id, scratchpad, apex_home=self.apex_home)

    def _process_messages(self, agent_name: str, task_id: str | None, parsed: dict[str, Any]) -> None:
        messages = parsed.get("messages", [])
        if not isinstance(messages, list) or not messages:
            return
        with self._db() as conn:
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                to_agent = str(msg.get("to", ""))
                msg_type = str(msg.get("type", "request"))
                content = str(msg.get("content", ""))
                if to_agent and content:
                    conn.execute(
                        """
                        INSERT INTO agent_messages (from_agent, to_agent, msg_type, content, task_id)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (agent_name, to_agent, msg_type, content, task_id),
                    )
            conn.commit()

    def _process_task_status(
        self,
        agent_name: str,
        task_id: str,
        session_id: str,
        parsed: dict[str, Any],
    ) -> None:
        status = parsed.get("status", {})
        state = status.get("state", "") if isinstance(status, dict) else str(status or "")
        with self._db() as conn:
            if state == "needs_review":
                stakes = status.get("stakes", "low") if isinstance(status, dict) else "low"
                conn.execute(
                    """
                    INSERT INTO reviews (task_id, agent_name, output_ref, stakes)
                    VALUES (?, ?, ?, ?)
                    """,
                    (task_id, agent_name, session_id, stakes or "low"),
                )
                conn.execute(
                    "UPDATE tasks SET review_status = 'pending', status = 'review' WHERE id = ?",
                    (task_id,),
                )
            elif state == "done":
                conn.execute(
                    """
                    UPDATE tasks
                    SET status = 'done', completed_at = datetime('now'), checked_out_by = NULL
                    WHERE id = ?
                    """,
                    (task_id,),
                )
            elif state == "blocked":
                conn.execute("UPDATE tasks SET status = 'blocked' WHERE id = ?", (task_id,))
            elif state == "unknown":
                conn.execute("UPDATE tasks SET status = 'blocked' WHERE id = ?", (task_id,))
            else:
                conn.execute("UPDATE tasks SET checked_out_by = NULL WHERE id = ?", (task_id,))
            conn.commit()

    def _build_spawn_context(self, agent_name: str, task_id: str) -> str:
        parts: list[str] = []
        with self._patch_spawn_context(agent_name, task_id):
            evidence = spawn_context.build_evidence()
            if evidence:
                parts.append(evidence)
            learning = spawn_context.build_learning()
            if learning:
                parts.append(learning)
        return "\n\n".join(parts)

    @contextmanager
    def _patch_spawn_context(self, agent_name: str, task_id: str) -> Iterator[None]:
        original = (
            spawn_context.APEX_HOME,
            spawn_context.DB_PATH,
            spawn_context.AGENT_NAME,
            spawn_context.TASK_ID,
        )
        spawn_context.APEX_HOME = str(self.apex_home)
        spawn_context.DB_PATH = str(self.db_path)
        spawn_context.AGENT_NAME = agent_name
        spawn_context.TASK_ID = task_id
        try:
            yield
        finally:
            (
                spawn_context.APEX_HOME,
                spawn_context.DB_PATH,
                spawn_context.AGENT_NAME,
                spawn_context.TASK_ID,
            ) = original

    @contextmanager
    def _temporary_env(self, **updates: str) -> Iterator[None]:
        old_values = {key: os.environ.get(key) for key in updates}
        for key, value in updates.items():
            os.environ[key] = value
        try:
            yield
        finally:
            for key, previous in old_values.items():
                if previous is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = previous

    def _get_inbox(self, agent_name: str) -> str:
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT from_agent, content FROM agent_messages
                WHERE to_agent = ? AND status = 'pending'
                ORDER BY priority ASC LIMIT 5
                """,
                (agent_name,),
            ).fetchall()
        return "\n".join(f"{row['from_agent']}|{row['content']}" for row in rows)

    def _mark_inbox_read(self, agent_name: str) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE agent_messages SET status = 'read' WHERE to_agent = ? AND status = 'pending'",
                (agent_name,),
            )
            conn.commit()

    def _get_task_info(self, task_id: str) -> str:
        with self._db() as conn:
            row = conn.execute(
                """
                SELECT t.title, t.description, g.name
                FROM tasks t
                LEFT JOIN goals g ON t.goal_id = g.id
                WHERE t.id = ?
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return ""
        values = [str(row["title"] or ""), str(row["description"] or ""), str(row["name"] or "")]
        return "|".join(values)

    def _checkout_task(self, agent_name: str, task_id: str) -> None:
        with self._db() as conn:
            row = conn.execute(
                """
                SELECT checked_out_by FROM tasks
                WHERE id = ? AND checked_out_by IS NOT NULL AND checked_out_by != ?
                """,
                (task_id, agent_name),
            ).fetchone()
            if row is not None:
                raise RuntimeError(f"Task {task_id} checked out by {row['checked_out_by']}")
            conn.execute(
                """
                UPDATE tasks
                SET checked_out_by = ?, checked_out_at = datetime('now'),
                    status = CASE WHEN status = 'backlog' THEN 'in_progress' ELSE status END
                WHERE id = ?
                """,
                (agent_name, task_id),
            )
            conn.commit()

    def _update_agent_status_active(self, agent_name: str, session_id: str) -> None:
        with self._db() as conn:
            conn.execute(
                """
                UPDATE agent_status
                SET status = 'active', last_heartbeat = datetime('now'), session_id = ?
                WHERE agent_name = ?
                """,
                (session_id, agent_name),
            )
            conn.commit()

    def _update_agent_status_idle(self, agent_name: str) -> None:
        with self._db() as conn:
            conn.execute(
                """
                UPDATE agent_status
                SET status = 'idle', current_task = NULL, last_heartbeat = datetime('now')
                WHERE agent_name = ?
                """,
                (agent_name,),
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()
