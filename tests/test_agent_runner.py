from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kernel.agent_runner import AgentRunner


class AgentRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.apex_home = Path(self.temp_dir.name)
        self._create_layout()
        self._create_database()
        self._seed_agent("builder")
        self._seed_goal_and_task("task-001")
        self.runner = AgentRunner(apex_home=self.apex_home)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_done_updates_task_and_messages(self) -> None:
        self._insert_pending_message()
        response = json.dumps(
            {
                "actions_taken": "checked task",
                "observations": "found result",
                "proposed_output": "done output",
                "messages": [{"to": "critic", "type": "request", "content": "please review"}],
                "scratchpad_update": "note this",
                "status": "done",
            }
        )

        with patch.object(self.runner, "_build_spawn_context", return_value="## Search Evidence\n(none available)"), patch(
            "kernel.agent_runner.call_model.call_ollama",
            return_value=response,
        ):
            result = self.runner.run("builder", "task-001")

        self.assertEqual(result["parsed"]["status"]["state"], "done")
        with self._connect() as conn:
            task = conn.execute("SELECT status, checked_out_by FROM tasks WHERE id = 'task-001'").fetchone()
            agent = conn.execute("SELECT status FROM agent_status WHERE agent_name = 'builder'").fetchone()
            inbox = conn.execute("SELECT status FROM agent_messages WHERE to_agent = 'builder'").fetchone()
            outbound = conn.execute(
                """
                SELECT to_agent, msg_type, content
                FROM agent_messages
                WHERE from_agent = 'builder'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()
            session = conn.execute(
                "SELECT context, task_id FROM agent_sessions WHERE agent_name = 'builder' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()

        self.assertEqual(task["status"], "done")
        self.assertIsNone(task["checked_out_by"])
        self.assertEqual(agent["status"], "idle")
        self.assertEqual(inbox["status"], "read")
        self.assertEqual(outbound["to_agent"], "critic")
        self.assertEqual(outbound["msg_type"], "request")
        self.assertEqual(outbound["content"], "please review")
        self.assertEqual(session["task_id"], "task-001")
        self.assertIn('"status": "done"', session["context"])

    def test_run_needs_review_queues_review_and_normalizes_invalid_target(self) -> None:
        response = json.dumps(
            {
                "actions_taken": "completed draft",
                "observations": "needs validation",
                "proposed_output": "draft",
                "messages": [{"to": "not-real", "type": "alert", "content": "bad target"}],
                "scratchpad_update": "review note",
                "status": "needs_review:high",
            }
        )

        with patch.object(self.runner, "_build_spawn_context", return_value=""), patch(
            "kernel.agent_runner.call_model.call_ollama",
            return_value=response,
        ):
            result = self.runner.run("builder", "task-001")

        self.assertEqual(result["parsed"]["status"]["state"], "needs_review")
        with self._connect() as conn:
            task = conn.execute("SELECT status, review_status FROM tasks WHERE id = 'task-001'").fetchone()
            review = conn.execute(
                "SELECT task_id, agent_name, output_ref, stakes FROM reviews WHERE task_id = 'task-001'"
            ).fetchone()
            message = conn.execute(
                """
                SELECT to_agent, msg_type, content
                FROM agent_messages
                WHERE from_agent = 'builder'
                ORDER BY id DESC LIMIT 1
                """
            ).fetchone()

        self.assertEqual(task["status"], "review")
        self.assertEqual(task["review_status"], "pending")
        self.assertEqual(review["agent_name"], "builder")
        self.assertEqual(review["stakes"], "high")
        self.assertEqual(review["output_ref"], result["session_id"])
        self.assertEqual(message["to_agent"], "apex")
        self.assertEqual(message["msg_type"], "escalation")
        self.assertIn("[invalid target: not-real]", message["content"])

    def test_run_releases_agent_on_checkout_conflict(self) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE tasks SET checked_out_by = 'critic' WHERE id = 'task-001'")
            conn.commit()

        with patch.object(self.runner, "_build_spawn_context", return_value=""):
            with self.assertRaises(RuntimeError):
                self.runner.run("builder", "task-001")

        with self._connect() as conn:
            agent = conn.execute("SELECT status FROM agent_status WHERE agent_name = 'builder'").fetchone()
            task = conn.execute("SELECT checked_out_by, status FROM tasks WHERE id = 'task-001'").fetchone()

        self.assertEqual(agent["status"], "idle")
        self.assertEqual(task["checked_out_by"], "critic")
        self.assertEqual(task["status"], "backlog")

    def _create_layout(self) -> None:
        (self.apex_home / "db").mkdir(parents=True, exist_ok=True)
        (self.apex_home / "kernel").mkdir(parents=True, exist_ok=True)
        (self.apex_home / "templates" / "startup-chief-of-staff" / "workspace").mkdir(parents=True, exist_ok=True)
        (self.apex_home / "templates" / "startup-chief-of-staff" / "workspace" / "MEMORY.md").write_text(
            "Shared memory\n",
            encoding="utf-8",
        )

    def _create_database(self) -> None:
        schema = Path(__file__).resolve().parents[1] / "db" / "schema.sql"
        conn = sqlite3.connect(self.apex_home / "db" / "apex_state.db")
        conn.executescript(schema.read_text(encoding="utf-8"))
        conn.commit()
        conn.close()

    def _seed_agent(self, agent_name: str) -> None:
        agent_dir = self.apex_home / "templates" / "startup-chief-of-staff" / "agents" / agent_name
        (agent_dir / "constraints").mkdir(parents=True, exist_ok=True)
        (agent_dir / "workspace").mkdir(parents=True, exist_ok=True)
        (agent_dir / "workspace" / "scratchpad.md").write_text("", encoding="utf-8")
        (agent_dir / "constraints" / "hard-rules.md").write_text("Do not fabricate.\n", encoding="utf-8")
        (agent_dir / "agent.json").write_text(
            json.dumps(
                {
                    "name": agent_name,
                    "role": "development",
                    "description": "Writes code",
                    "model": {"primary": "qwen3.5-apex", "fallback": "claude-sonnet"},
                    "api_config": {"temperature": 0.1},
                }
            ),
            encoding="utf-8",
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_status (agent_name, status, model_active, meta)
                VALUES (?, 'idle', 'qwen3.5-apex', ?)
                """,
                (agent_name, json.dumps({"config_path": str(agent_dir / "agent.json")})),
            )
            conn.commit()

    def _seed_goal_and_task(self, task_id: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO goals (id, name) VALUES ('goal-001', 'Test Goal')")
            conn.execute(
                """
                INSERT INTO tasks (id, goal_id, title, description, status)
                VALUES (?, 'goal-001', 'Test Task', 'Investigate behavior', 'backlog')
                """,
                (task_id,),
            )
            conn.commit()

    def _insert_pending_message(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_messages (from_agent, to_agent, msg_type, content, status)
                VALUES ('apex', 'builder', 'request', 'handle this', 'pending')
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.apex_home / "db" / "apex_state.db")
        conn.row_factory = sqlite3.Row
        return conn


if __name__ == "__main__":
    unittest.main()
