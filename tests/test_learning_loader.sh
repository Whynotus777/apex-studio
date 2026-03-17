#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

cd "$APEX_HOME"

echo "=== APEX Learning Loader Tests ==="
echo ""

python3 - <<'PYEOF'
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from kernel.learning import AgentLearning

with tempfile.TemporaryDirectory() as tmpdir:
    db_path = Path(tmpdir) / "learning_loader_test.db"
    learning = AgentLearning(db_path)
    workspace_id = "ws-loader-001"

    learning.set_preference(workspace_id, "platform", "default", "x")
    learning.set_preference(workspace_id, "source_preference", "source_type", "primary sources")
    learning.set_preference(workspace_id, "preferred_domain", "domain_1", "openai.com")
    learning.set_preference(workspace_id, "style_preference", "voice", "crisp")
    learning.add_voice_sample(workspace_id, "x", "Short, sharp, high-signal takes.")
    learning.set_platform_profile(
        workspace_id,
        "x",
        "Use compact lines and a strong first sentence.",
        "sharp and opinionated",
        "followers",
    )
    learning.record_performance(
        workspace_id,
        "task-loader-001",
        "x",
        {
            "likes": 200,
            "comments": 18,
            "reposts": 25,
            "impressions": 10000,
            "topic_keywords": ["ai agents", "distribution"],
            "structure_type": "thread",
            "hook_style": "bold claim",
            "source_domains": ["openai.com"],
            "post_length": "short",
            "time_posted": "morning",
        },
    )

    env = os.environ.copy()
    env["APEX_DB"] = str(db_path)

    def run_loader(agent_name: str) -> str:
        return subprocess.check_output(
            ["python3", "kernel/learning_loader.py", agent_name, "task-loader-001"],
            text=True,
            env=env,
        )

    scout_output = run_loader("ws-loader-001-scout")
    assert "## Learned Search Preferences" in scout_output, scout_output
    assert "primary sources" in scout_output, scout_output
    assert "openai.com" in scout_output, scout_output
    print("PASS scout loader returns search preference context")

    writer_output = run_loader("ws-loader-001-writer")
    assert "## Writer Learning Context — x" in writer_output, writer_output
    assert "Short, sharp, high-signal takes." in writer_output, writer_output
    assert "Optimization mode: followers" in writer_output, writer_output
    assert "ai agents" in writer_output, writer_output
    print("PASS writer loader returns voice, platform, and performance context")

    critic_output = run_loader("ws-loader-001-critic")
    assert "## Critic Learning Context — x" in critic_output, critic_output
    assert "Optimization target: followers" in critic_output, critic_output
    assert "hook strength" in critic_output.lower(), critic_output
    print("PASS critic loader returns platform scoring context")

    strategist_output = run_loader("ws-loader-001-strategist")
    assert strategist_output == "", strategist_output
    print("PASS non-supported roles return empty context")

print("")
print("=== Learning Loader Tests Complete ===")
PYEOF
