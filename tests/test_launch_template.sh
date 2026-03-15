#!/bin/bash
# test_launch_template.sh — Phase C template launch tests
# Tests launch_template(), list_templates(), and get_template() via kernel/api.py
# No model calls — pure SQLite + filesystem, runs in seconds.
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

echo "=== APEX Phase C Template Launch Tests ==="
echo ""

python3 - <<'PYEOF'
import sys, sqlite3, json
sys.path.insert(0, ".")
from kernel.api import ApexKernel

k = ApexKernel()

passed = 0
failed = 0

def ok(label):
    global passed
    passed += 1
    print(f"  PASS  {label}")

def fail(label, detail=""):
    global failed
    failed += 1
    print(f"  FAIL  {label}" + (f": {detail}" if detail else ""))

# ── Cleanup: remove any agents that may linger from prior runs ─────────
def cleanup():
    conn = sqlite3.connect(str(k.db_path))
    for agent in ("apex", "scout", "analyst", "builder", "critic"):
        conn.execute("DELETE FROM agent_status WHERE agent_name = ?", (agent,))
        conn.execute("DELETE FROM permissions WHERE agent_id = ?", (agent,))
        conn.execute("DELETE FROM budgets WHERE agent_id = ?", (agent,))
        conn.execute("DELETE FROM spend_log WHERE agent_id = ?", (agent,))
    conn.commit()
    conn.close()

cleanup()

# ══════════════════════════════════════════════════════════════════════
# list_templates
# ══════════════════════════════════════════════════════════════════════
print("--- list_templates ---")

try:
    templates = k.list_templates()
    ids = [t["id"] for t in templates]
    assert "startup-chief-of-staff" in ids, f"startup-chief-of-staff missing: {ids}"
    assert "research-assistant" in ids, f"research-assistant missing: {ids}"
    ok(f"list_templates returns both templates ({len(templates)} total)")
except Exception as e:
    fail("list_templates", e)

try:
    templates = k.list_templates()
    cos = next(t for t in templates if t["id"] == "startup-chief-of-staff")
    assert cos["agent_count"] == 5, f"expected 5 agents, got {cos['agent_count']}"
    assert cos["category"] == "startup"
    ok("startup-chief-of-staff manifest: 5 agents, category=startup")
except Exception as e:
    fail("list_templates startup-chief-of-staff details", e)

try:
    templates = k.list_templates()
    ra = next(t for t in templates if t["id"] == "research-assistant")
    assert ra["agent_count"] == 3, f"expected 3 agents, got {ra['agent_count']}"
    assert ra["category"] == "research"
    assert ra["pipeline"] == ["discover", "analyze", "validate"]
    ok("research-assistant manifest: 3 agents, category=research, 3-stage pipeline")
except Exception as e:
    fail("list_templates research-assistant details", e)

# ══════════════════════════════════════════════════════════════════════
# get_template
# ══════════════════════════════════════════════════════════════════════
print("\n--- get_template ---")

try:
    manifest = k.get_template("startup-chief-of-staff")
    assert manifest["name"] == "Startup Chief of Staff"
    assert len(manifest["agents"]) == 5
    ok("get_template startup-chief-of-staff returns full manifest")
except Exception as e:
    fail("get_template startup-chief-of-staff", e)

try:
    manifest = k.get_template("research-assistant")
    assert manifest["name"] == "Research Assistant"
    assert len(manifest["agents"]) == 3
    agent_names = [a["name"] for a in manifest["agents"]]
    assert "scout" in agent_names
    assert "analyst" in agent_names
    assert "critic" in agent_names
    ok("get_template research-assistant: 3 agents (scout, analyst, critic)")
except Exception as e:
    fail("get_template research-assistant", e)

try:
    k.get_template("nonexistent-template-xyz")
    fail("get_template nonexistent should raise FileNotFoundError")
except FileNotFoundError:
    ok("get_template raises FileNotFoundError for unknown template")
except Exception as e:
    fail("get_template nonexistent unexpected error", e)

# ══════════════════════════════════════════════════════════════════════
# launch_template — startup-chief-of-staff
# ══════════════════════════════════════════════════════════════════════
print("\n--- launch_template: startup-chief-of-staff ---")

try:
    result = k.launch_template("startup-chief-of-staff")
    assert result["template_name"] == "Startup Chief of Staff"
    assert result["template_id"] == "startup-chief-of-staff"
    ok(f"launch_template returned correctly: template_name={result['template_name']}")
except Exception as e:
    fail("launch_template startup-chief-of-staff basic call", e)
    sys.exit(1)

try:
    created = result["agents_created"]
    assert set(created) == {"apex", "scout", "analyst", "builder", "critic"}, \
        f"expected all 5 agents, got {created}"
    ok(f"launch_template created all 5 agents: {sorted(created)}")
except Exception as e:
    fail("launch_template agents_created", e)

try:
    # Verify all 5 agents exist in agent_status
    conn = sqlite3.connect(str(k.db_path))
    for agent in ("apex", "scout", "analyst", "builder", "critic"):
        row = conn.execute(
            "SELECT status FROM agent_status WHERE agent_name = ?", (agent,)
        ).fetchone()
        assert row is not None, f"agent '{agent}' missing from agent_status"
        assert row[0] == "idle", f"agent '{agent}' status={row[0]}, expected idle"
    conn.close()
    ok("all 5 agents present in agent_status with status=idle")
except Exception as e:
    fail("agent_status verification", e)

try:
    # Verify permissions were applied
    assert result["permissions_applied"] > 0, "expected permissions_applied > 0"
    perms = k.get_agent_permissions("apex")
    assert len(perms) > 0, "apex should have at least one permission"
    ok(f"permissions applied: {result['permissions_applied']} total, apex has {len(perms)}")
except Exception as e:
    fail("permissions_applied verification", e)

# ── idempotency: launch again should NOT create duplicates ─────────────
try:
    result2 = k.launch_template("startup-chief-of-staff")
    assert result2["agents_created"] == [], \
        f"second launch should create 0 new agents, got {result2['agents_created']}"
    ok("launch_template is idempotent: second launch creates 0 new agents")
except Exception as e:
    fail("launch_template idempotency", e)

# ══════════════════════════════════════════════════════════════════════
# launch_template — research-assistant (independent, different agents)
# The 3 agents (scout, analyst, critic) overlap with startup-chief-of-staff!
# Because they share names (scout/analyst/critic), they'll be in DB already.
# Clean up and relaunch fresh to test independently.
# ══════════════════════════════════════════════════════════════════════
print("\n--- launch_template: research-assistant ---")

# Clean up agent_status for the 3 research agents so we can test fresh creation
try:
    cleanup()
    ok("cleanup complete — fresh agent_status for research-assistant test")
except Exception as e:
    fail("cleanup before research-assistant test", e)

try:
    result_ra = k.launch_template("research-assistant")
    assert result_ra["template_name"] == "Research Assistant"
    assert result_ra["template_id"] == "research-assistant"
    ok(f"launch_template research-assistant returned correctly")
except Exception as e:
    fail("launch_template research-assistant basic call", e)
    sys.exit(1)

try:
    created_ra = result_ra["agents_created"]
    assert set(created_ra) == {"scout", "analyst", "critic"}, \
        f"expected scout/analyst/critic, got {created_ra}"
    ok(f"research-assistant created 3 agents: {sorted(created_ra)}")
except Exception as e:
    fail("research-assistant agents_created", e)

try:
    # Verify only 3 agents in DB (no apex or builder from this template)
    conn = sqlite3.connect(str(k.db_path))
    all_agents = [r[0] for r in conn.execute("SELECT agent_name FROM agent_status").fetchall()]
    conn.close()
    assert "apex" not in all_agents, f"apex should not exist after research-assistant launch, found: {all_agents}"
    assert "builder" not in all_agents, f"builder should not exist, found: {all_agents}"
    assert len(all_agents) == 3, f"expected exactly 3 agents, got {len(all_agents)}: {all_agents}"
    ok("research-assistant: exactly 3 agents, no apex or builder")
except Exception as e:
    fail("research-assistant agent isolation", e)

try:
    # Verify budgets were applied (research-assistant has default_budgets)
    assert result_ra["budgets_applied"] > 0, "expected budgets_applied > 0"
    statuses = k.get_budget_status("scout")
    budget_types = {b["budget_type"] for b in statuses}
    assert "api_calls" in budget_types, f"api_calls budget missing: {budget_types}"
    assert "tool_cost" in budget_types, f"tool_cost budget missing: {budget_types}"
    ok(f"research-assistant budgets applied: {result_ra['budgets_applied']} total, scout has {budget_types}")
except Exception as e:
    fail("research-assistant budgets_applied", e)

try:
    # Verify permissions were applied
    assert result_ra["permissions_applied"] > 0
    perms = k.get_agent_permissions("analyst")
    assert len(perms) > 0
    ok(f"research-assistant permissions applied: {result_ra['permissions_applied']} total")
except Exception as e:
    fail("research-assistant permissions_applied", e)

try:
    # Verify model config from template
    manifest = k.get_template("research-assistant")
    scout_cfg = next(a for a in manifest["agents"] if a["name"] == "scout")
    assert scout_cfg["heartbeat"] == "0 */6 * * *", f"unexpected heartbeat: {scout_cfg['heartbeat']}"
    assert scout_cfg["api_config"]["think"] == False
    ok("research-assistant scout: heartbeat every 6h, think=false")
except Exception as e:
    fail("research-assistant config verification", e)

# ── Summary ───────────────────────────────────────────────────────────
print(f"\n{'='*46}")
print(f"  Tests passed: {passed}")
print(f"  Tests failed: {failed}")
print(f"{'='*46}")

if failed > 0:
    sys.exit(1)
PYEOF

echo ""
echo "=== Template Launch Tests Complete ==="
