from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kernel.learning import AgentLearning


def _parse_agent_name(agent_name: str) -> tuple[str, str]:
    if "-" not in agent_name:
        return "", agent_name.strip().lower()
    workspace_id, role = agent_name.rsplit("-", 1)
    return workspace_id, role.strip().lower()


def _resolve_platform(learning: AgentLearning, workspace_id: str) -> str:
    if not workspace_id:
        return "linkedin"
    prefs = learning.get_preferences(workspace_id, "platform")
    if prefs:
        return str(prefs[0]["value"]).strip().lower() or "linkedin"
    return "linkedin"


def load_learning_context(agent_name: str, task_id: str, db_path: str | Path) -> str:
    del task_id  # reserved for future task-aware learning context
    learning = AgentLearning(db_path)
    workspace_id, role = _parse_agent_name(agent_name)
    platform = _resolve_platform(learning, workspace_id)

    if role == "scout":
        return learning.format_for_scout(workspace_id)
    if role == "writer":
        return learning.format_for_writer(workspace_id, platform)
    if role == "critic":
        return learning.format_for_critic(workspace_id, platform)
    return ""


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: learning_loader.py <agent_name> <task_id>", file=sys.stderr)
        return 1

    db_path = os.environ.get("APEX_DB") or str(
        Path(os.environ.get("APEX_HOME") or Path(__file__).resolve().parents[1]) / "db" / "apex_state.db"
    )
    agent_name = sys.argv[1]
    task_id = sys.argv[2]
    print(load_learning_context(agent_name, task_id, db_path), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
