#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

cd "$APEX_HOME"

echo "=== APEX Learning Tests ==="
echo ""

python3 - <<'PYEOF'
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from kernel.learning import AgentLearning

with tempfile.TemporaryDirectory() as tmpdir:
    db_path = Path(tmpdir) / "learning_test.db"
    learning = AgentLearning(db_path)
    workspace_id = "ws-learning-001"

    print("--- Preferences CRUD ---")
    learning.set_preference(workspace_id, "style_preference", "tone", "direct")
    learning.set_preference(workspace_id, "style_preference", "tone", "sharp")
    learning.set_preference(workspace_id, "source_preference", "source_type", "primary sources")
    learning.set_preference(workspace_id, "preferred_domain", "domain_1", "bain.com")
    prefs = learning.get_preferences(workspace_id, "style_preference")
    assert prefs == [{"key": "tone", "value": "sharp"}], prefs
    print("PASS preference upsert works")

    print("\n--- Voice Samples ---")
    for idx in range(12):
        learning.add_voice_sample(workspace_id, "linkedin", f"Voice sample {idx}")
    voice_samples = learning.get_voice_samples(workspace_id, "linkedin")
    assert len(voice_samples) == 10, voice_samples
    assert voice_samples[0] == "Voice sample 11", voice_samples
    assert "Voice sample 0" not in voice_samples and "Voice sample 1" not in voice_samples, voice_samples
    print("PASS voice samples capped at 10 per platform")

    print("\n--- Platform Profiles ---")
    default_profile = learning.get_platform_profile(workspace_id, "linkedin")
    assert default_profile["optimization_mode"] == "authority", default_profile
    learning.set_platform_profile(
        workspace_id,
        "linkedin",
        "Use short punchy paragraphs and one CTA.",
        "credible and bold",
        "lead_gen",
    )
    custom_profile = learning.get_platform_profile(workspace_id, "linkedin")
    assert custom_profile["optimization_mode"] == "lead_gen", custom_profile
    assert "short punchy paragraphs" in custom_profile["format_rules"], custom_profile
    print("PASS platform profiles load defaults and allow overrides")

    print("\n--- Performance Recording ---")
    learning.record_performance(
        workspace_id,
        "task-1",
        "linkedin",
        {
            "likes": 120,
            "comments": 22,
            "reposts": 8,
            "impressions": 5000,
            "shares": 4,
            "follows": 6,
            "topic_keywords": ["ai agents", "gtm"],
            "structure_type": "listicle",
            "hook_style": "contrarian",
            "source_domains": ["bain.com", "openai.com"],
            "post_length": "medium",
            "time_posted": "morning",
        },
    )
    learning.record_performance(
        workspace_id,
        "task-2",
        "linkedin",
        {
            "likes": 40,
            "comments": 3,
            "reposts": 1,
            "impressions": 4000,
            "shares": 1,
            "follows": 1,
            "topic_keywords": ["saas"],
            "structure_type": "narrative",
            "hook_style": "question",
            "source_domains": ["techcrunch.com"],
            "post_length": "long",
            "time_posted": "afternoon",
        },
    )
    history = learning.get_performance_history(workspace_id, "linkedin")
    assert len(history) == 2, history
    assert "engagement_score" in history[0]["metrics"], history[0]
    print("PASS performance history stored with computed engagement score")

    print("\n--- Top Patterns ---")
    patterns = learning.get_top_patterns(workspace_id, "linkedin", n=3)
    assert patterns["topic_keywords"][0] == "ai agents", patterns
    assert patterns["structure_type"][0] == "listicle", patterns
    assert patterns["hook_style"][0] == "contrarian", patterns
    assert patterns["source_domains"][0] == "bain.com", patterns
    print("PASS top patterns extraction favors high-engagement patterns")

    print("\n--- Prompt Formatting ---")
    scout_prompt = learning.format_for_scout(workspace_id)
    assert "primary sources" in scout_prompt, scout_prompt
    assert "bain.com" in scout_prompt, scout_prompt
    print("PASS scout prompt formatting includes source preferences")

    writer_prompt = learning.format_for_writer(workspace_id, "linkedin")
    assert "Voice sample 11" in writer_prompt, writer_prompt
    assert "lead_gen" in writer_prompt, writer_prompt
    assert "ai agents" in writer_prompt, writer_prompt
    assert "credible and bold" in writer_prompt, writer_prompt
    print("PASS writer prompt formatting includes voice, profile, and patterns")

    critic_prompt = learning.format_for_critic(workspace_id, "linkedin")
    assert "Optimization target: lead_gen" in critic_prompt, critic_prompt
    assert "depth of insight" in critic_prompt, critic_prompt
    print("PASS critic prompt formatting includes platform-specific scoring guidance")

    tiktok_profile = learning.get_platform_profile(workspace_id, "tiktok")
    instagram_profile = learning.get_platform_profile(workspace_id, "instagram")
    x_profile = learning.get_platform_profile(workspace_id, "x")
    assert tiktok_profile["optimization_mode"] == "watch_time", tiktok_profile
    assert instagram_profile["optimization_mode"] == "saves", instagram_profile
    assert x_profile["optimization_mode"] == "virality", x_profile
    print("PASS built-in platform defaults exist for x, tiktok, instagram")

print("")
print("=== Learning Tests Complete ===")
PYEOF
