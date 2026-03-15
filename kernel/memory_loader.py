from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from kernel.memory import DurableMemory, SessionMemory, WorkingMemory


def load_agent_memory(agent_id: str, apex_home: str | Path | None = None) -> dict[str, Any]:
    root = Path(apex_home or os.environ.get("APEX_HOME") or Path(__file__).resolve().parents[1]).resolve()
    session_memory = SessionMemory(root)
    working_memory = WorkingMemory(root)
    durable_memory = DurableMemory(root)

    latest = session_memory.get_latest(agent_id)
    return {
        "session_context": latest.get("context", ""),
        "working_memory": working_memory.read(agent_id),
        "durable_memory": durable_memory.read(),
    }


def save_agent_memory(
    agent_id: str,
    session_id: str,
    scratchpad_update: str,
    apex_home: str | Path | None = None,
) -> None:
    root = Path(apex_home or os.environ.get("APEX_HOME") or Path(__file__).resolve().parents[1]).resolve()
    session_memory = SessionMemory(root)
    working_memory = WorkingMemory(root)

    context = os.environ.get("MEMORY_SESSION_CONTEXT", scratchpad_update)
    task_id = os.environ.get("APEX_TASK_ID") or None

    if scratchpad_update and scratchpad_update != "None":
        try:
            working_memory.append(agent_id, scratchpad_update, session_id)
        except FileNotFoundError:
            pass  # workspace-scoped agents share a template scratchpad; skip file write

    session_memory.save(agent_id, session_id, task_id, context)


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: memory_loader.py <load|save> <agent_id> [session_id] [scratchpad_text]", file=sys.stderr)
        return 1

    command = sys.argv[1]
    agent_id = sys.argv[2]

    if command == "load":
        print(json.dumps(load_agent_memory(agent_id)))
        return 0

    if command == "save":
        if len(sys.argv) < 5:
            print(
                "Usage: memory_loader.py save <agent_id> <session_id> <scratchpad_text>",
                file=sys.stderr,
            )
            return 1
        session_id = sys.argv[3]
        scratchpad_text = sys.argv[4]
        save_agent_memory(agent_id, session_id, scratchpad_text)
        return 0

    print(f"Unknown command: {command}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
