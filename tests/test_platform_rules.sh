#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

cd "$APEX_HOME"

echo "=== APEX Platform Rules Tests ==="
echo ""

python3 - <<'PYEOF'
import sys

sys.path.insert(0, ".")

from kernel.platform_rules import get_critic_adjustments, get_writer_instructions

print("--- Writer Instructions ---")
linkedin = get_writer_instructions("linkedin")
assert "Prefer strong openings" in linkedin, linkedin
assert "channel-specific variants" in linkedin, linkedin
print("PASS linkedin instructions read from soft-preferences.md")

x_rules = get_writer_instructions("x")
assert "X (Twitter) Writer Rules" in x_rules, x_rules
assert "280 character hard limit" in x_rules, x_rules
print("PASS x instructions read from x-rules.md")

tiktok = get_writer_instructions("tiktok")
assert "TikTok Writer Rules" in tiktok, tiktok
assert "Hook → Context → Insight → CTA" in tiktok, tiktok
print("PASS tiktok instructions read from tiktok-rules.md")

instagram = get_writer_instructions("instagram")
assert "Instagram Writer Rules" in instagram, instagram
assert "caption-first" in instagram.lower(), instagram
print("PASS instagram instructions returned inline")

print("\n--- Critic Adjustments ---")
linkedin_adj = get_critic_adjustments("linkedin")
assert "depth of insight" in linkedin_adj.lower(), linkedin_adj
print("PASS linkedin critic adjustment favors depth")

x_adj = get_critic_adjustments("x")
assert "punchiness" in x_adj.lower(), x_adj
print("PASS x critic adjustment favors punchiness")

tiktok_adj = get_critic_adjustments("tiktok")
assert "hook strength" in tiktok_adj.lower(), tiktok_adj
print("PASS tiktok critic adjustment favors hook strength")

instagram_adj = get_critic_adjustments("instagram")
assert "save-worthiness" in instagram_adj.lower(), instagram_adj
print("PASS instagram critic adjustment favors visual save-worthiness")

try:
    get_writer_instructions("youtube")
except ValueError:
    print("PASS unsupported platform raises ValueError")
else:
    raise AssertionError("Expected ValueError for unsupported platform")

print("\n=== Platform Rules Tests Complete ===")
PYEOF
