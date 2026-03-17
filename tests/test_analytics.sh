#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

cd "$APEX_HOME"

echo "=== APEX Analytics Tests ==="
echo ""

python3 - <<'PYEOF'
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from adapters.telegram.analytics import (
    format_digest_for_telegram,
    generate_weekly_digest,
    record_engagement,
    track_publish,
)
from kernel.learning import AgentLearning

with tempfile.TemporaryDirectory() as tmpdir:
    db_path = Path(tmpdir) / "analytics_test.db"
    workspace_id = "ws-analytics-001"

    learning = AgentLearning(db_path)
    learning.set_platform_profile(
        workspace_id,
        "linkedin",
        "Use line breaks and an operator hook.",
        "credible and practical",
        "authority",
    )

    print("--- Track Publish ---")
    publish_id = track_publish(
        workspace_id,
        "task-001",
        "linkedin",
        "AI agents are changing GTM.\n\n- Faster research\n- Better positioning\n- Sharper content",
        "2026-03-16T14:00:00+00:00",
        db_path=db_path,
    )
    assert publish_id.startswith("perf-"), publish_id
    history = learning.get_performance_history(workspace_id, "linkedin")
    assert len(history) == 1, history
    assert history[0]["metrics"]["status"] == "published", history[0]
    assert history[0]["metrics"]["structure_type"] == "listicle", history[0]
    print("PASS track_publish stores derived content metadata")

    print("\n--- Record Engagement ---")
    engagement_id = record_engagement(
        workspace_id,
        "task-001",
        {
            "likes": 120,
            "comments": 14,
            "reposts": 9,
            "impressions": 5000,
            "shares": 6,
            "follows": 4,
            "source_domains": ["bain.com"],
        },
        db_path=db_path,
    )
    assert engagement_id.startswith("perf-"), engagement_id
    history = learning.get_performance_history(workspace_id, "linkedin")
    assert len(history) == 2, history
    assert history[0]["metrics"]["likes"] == 120, history[0]
    assert history[0]["metrics"]["status"] == "published", history[0]
    print("PASS record_engagement merges into the published record context")

    print("\n--- Weekly Digest ---")
    track_publish(
        workspace_id,
        "task-002",
        "x",
        "Hot take: most GTM teams still ship generic messaging.",
        "2026-03-15T12:00:00+00:00",
        db_path=db_path,
    )
    record_engagement(
        workspace_id,
        "task-002",
        {
            "platform": "x",
            "likes": 240,
            "comments": 22,
            "reposts": 35,
            "impressions": 12000,
            "shares": 12,
            "follows": 18,
            "source_domains": ["openai.com"],
        },
        db_path=db_path,
    )
    digest = generate_weekly_digest(workspace_id, db_path=db_path)
    assert digest["total_posts"] == 2, digest
    assert "linkedin" in digest["platforms"], digest
    assert "x" in digest["platforms"], digest
    assert digest["best_post"]["task_id"] in {"task-001", "task-002"}, digest
    print("PASS generate_weekly_digest summarizes recent performance")

    print("\n--- Telegram Formatting ---")
    text = format_digest_for_telegram(digest)
    assert "📈 Weekly Content Digest" in text, text
    assert "🏆 Best Post" in text, text
    assert "linkedin" in text and "x" in text, text
    assert "Hooks:" in text and "Topics:" in text, text
    print("PASS format_digest_for_telegram renders a readable digest")

print("")
print("=== Analytics Tests Complete ===")
PYEOF
