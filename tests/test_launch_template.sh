#!/bin/bash
# test_launch_template.sh — Phase C+D template launch + workspace scoping tests
# Tests launch_template(), list_templates(), get_template(), workspace CRUD,
# and simultaneous multi-template launch with zero agent name collisions.
# No model calls — pure SQLite + filesystem, runs in seconds.
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

echo "=== APEX Phase C+D Template + Workspace Tests ==="
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

# ── Cleanup all workspace-scoped test data ─────────────────────────────
def cleanup_all():
    conn = sqlite3.connect(str(k.db_path))
    # Remove workspace rows
    conn.execute("DELETE FROM workspaces WHERE id LIKE 'ws-%'")
    # Remove workspace-scoped agents (ws-xxx-agentname pattern)
    conn.execute("DELETE FROM agent_status WHERE workspace_id IS NOT NULL")
    conn.execute("DELETE FROM permissions WHERE workspace_id IS NOT NULL")
    conn.execute("DELETE FROM budgets WHERE workspace_id IS NOT NULL")
    conn.execute("DELETE FROM spend_log WHERE agent_id LIKE 'ws-%'")
    # Also remove the global agents that test_primitives.sh leaves behind
    for agent in ("apex", "scout", "analyst", "builder", "critic"):
        conn.execute("DELETE FROM agent_status WHERE agent_name = ?", (agent,))
        conn.execute("DELETE FROM permissions WHERE agent_id = ? AND workspace_id IS NULL", (agent,))
        conn.execute("DELETE FROM budgets WHERE agent_id = ? AND workspace_id IS NULL", (agent,))
    conn.commit()
    conn.close()

cleanup_all()

# ══════════════════════════════════════════════════════════════════════
# list_templates / get_template (Phase C — no change)
# ══════════════════════════════════════════════════════════════════════
print("--- list_templates / get_template ---")

try:
    templates = k.list_templates()
    ids = [t["id"] for t in templates]
    assert "startup-chief-of-staff" in ids and "research-assistant" in ids
    ok(f"list_templates: both templates present ({len(templates)} total)")
except Exception as e:
    fail("list_templates", e)

try:
    m = k.get_template("research-assistant")
    assert m["agent_count"] if "agent_count" in m else len(m["agents"]) == 3
    ok("get_template research-assistant OK")
except Exception as e:
    fail("get_template", e)

try:
    k.get_template("no-such-template")
    fail("get_template nonexistent should raise")
except FileNotFoundError:
    ok("get_template raises FileNotFoundError for unknown template")

# ══════════════════════════════════════════════════════════════════════
# Workspace CRUD
# ══════════════════════════════════════════════════════════════════════
print("\n--- Workspace CRUD ---")

try:
    ws_id = k.create_workspace("startup-chief-of-staff", name="test-workspace-1")
    assert ws_id.startswith("ws-"), f"expected ws- prefix, got {ws_id}"
    ok(f"create_workspace returns namespaced id: {ws_id}")
except Exception as e:
    fail("create_workspace", e)
    sys.exit(1)

try:
    ws = k.get_workspace(ws_id)
    assert ws["id"] == ws_id
    assert ws["template_id"] == "startup-chief-of-staff"
    assert ws["name"] == "test-workspace-1"
    assert ws["status"] == "active"
    ok("get_workspace returns correct fields")
except Exception as e:
    fail("get_workspace", e)

try:
    k.get_workspace("ws-does-not-exist")
    fail("get_workspace nonexistent should raise")
except ValueError:
    ok("get_workspace raises ValueError for unknown workspace")

try:
    ws_temp = k.create_workspace("research-assistant", name="temp-for-delete")
    k.delete_workspace(ws_temp)
    workspaces = k.list_workspaces()
    deleted = next((w for w in workspaces if w["id"] == ws_temp), None)
    assert deleted is not None and deleted["status"] == "deleted", \
        f"expected deleted status, got {deleted}"
    ok("delete_workspace sets status=deleted")
except Exception as e:
    fail("delete_workspace", e)

# Clean up the manually created workspace before launch tests
conn = sqlite3.connect(str(k.db_path))
conn.execute("DELETE FROM workspaces WHERE id = ?", (ws_id,))
conn.commit()
conn.close()

# ══════════════════════════════════════════════════════════════════════
# launch_template with workspace — startup-chief-of-staff
# ══════════════════════════════════════════════════════════════════════
print("\n--- launch_template with workspace: startup-chief-of-staff ---")

try:
    r1 = k.launch_template("startup-chief-of-staff")
    assert "workspace_id" in r1, "result must include workspace_id"
    ws1 = r1["workspace_id"]
    assert ws1.startswith("ws-"), f"workspace_id should start with ws-, got {ws1}"
    ok(f"launch_template auto-creates workspace: {ws1}")
except Exception as e:
    fail("launch_template startup-chief-of-staff", e)
    sys.exit(1)

try:
    created = r1["agents_created"]
    assert len(created) == 5, f"expected 5 agents, got {len(created)}: {created}"
    for name in ("apex", "scout", "analyst", "builder", "critic"):
        expected = f"{ws1}-{name}"
        assert expected in created, f"{expected} missing from {created}"
    ok(f"5 workspace-namespaced agents created: {sorted(created)}")
except Exception as e:
    fail("workspace-namespaced agent names", e)

try:
    ws_detail = k.get_workspace(ws1)
    assert ws_detail["agent_count"] == 5
    agent_names = [a["agent_name"] for a in ws_detail["agents"]]
    assert all(n.startswith(ws1 + "-") for n in agent_names), \
        f"all agents should be prefixed with workspace: {agent_names}"
    ok(f"get_workspace shows 5 agents all prefixed with {ws1}-")
except Exception as e:
    fail("workspace agent count and naming", e)

try:
    assert r1["permissions_applied"] > 0
    perms = k.get_agent_permissions(f"{ws1}-apex")
    assert len(perms) > 0
    ws_perms = [p for p in perms if p.get("workspace_id") == ws1]
    assert len(ws_perms) > 0, "permissions must carry workspace_id"
    ok(f"permissions workspace-scoped: {r1['permissions_applied']} applied, ws_id propagated")
except Exception as e:
    fail("workspace permissions", e)

# ══════════════════════════════════════════════════════════════════════
# launch_template with workspace — research-assistant (SIMULTANEOUSLY)
# Both templates share agent role names: scout, analyst, critic
# ══════════════════════════════════════════════════════════════════════
print("\n--- launch_template with workspace: research-assistant (simultaneous) ---")

try:
    r2 = k.launch_template("research-assistant")
    ws2 = r2["workspace_id"]
    assert ws2 != ws1, f"each launch must get a different workspace_id (both got {ws1})"
    assert ws2.startswith("ws-")
    ok(f"research-assistant gets distinct workspace: {ws2}")
except Exception as e:
    fail("launch_template research-assistant", e)
    sys.exit(1)

try:
    created2 = r2["agents_created"]
    assert len(created2) == 3, f"expected 3, got {created2}"
    for name in ("scout", "analyst", "critic"):
        expected = f"{ws2}-{name}"
        assert expected in created2, f"{expected} missing"
    ok(f"3 workspace-namespaced agents: {sorted(created2)}")
except Exception as e:
    fail("research-assistant agent names", e)

try:
    # budgets should be workspace-scoped
    assert r2["budgets_applied"] > 0
    statuses = k.get_budget_status(f"{ws2}-scout")
    ws_budgets = [b for b in statuses if b.get("workspace_id") == ws2]
    assert len(ws_budgets) > 0, "budgets must carry workspace_id"
    ok(f"budgets workspace-scoped: {r2['budgets_applied']} applied, ws_id propagated")
except Exception as e:
    fail("workspace budgets", e)

# ══════════════════════════════════════════════════════════════════════
# Zero-collision verification — the core test
# ══════════════════════════════════════════════════════════════════════
print("\n--- Zero-collision verification ---")

try:
    conn = sqlite3.connect(str(k.db_path))
    all_ws_agents = [
        r[0] for r in conn.execute(
            "SELECT agent_name FROM agent_status WHERE workspace_id IS NOT NULL"
        ).fetchall()
    ]
    conn.close()

    # Collect all agent names across both workspaces
    ws1_agents = set(r1["agents_created"])
    ws2_agents = set(r2["agents_created"])

    # No overlap at all
    collision = ws1_agents & ws2_agents
    assert not collision, f"NAME COLLISION between workspaces: {collision}"
    ok(f"ZERO collisions: ws1 has {len(ws1_agents)} agents, ws2 has {len(ws2_agents)}, no overlap")
except Exception as e:
    fail("zero-collision check", e)

try:
    # Verify shared role names (scout/analyst/critic) exist in both workspaces
    # under different namespaced names
    for role in ("scout", "analyst", "critic"):
        ws1_name = f"{ws1}-{role}"
        ws2_name = f"{ws2}-{role}"
        assert ws1_name in ws1_agents, f"{ws1_name} missing from ws1"
        assert ws2_name in ws2_agents, f"{ws2_name} missing from ws2"
        assert ws1_name != ws2_name, "should be different names"
    ok("shared role names (scout, analyst, critic) exist in both workspaces under distinct namespaces")
except Exception as e:
    fail("shared role namespace isolation", e)

try:
    # Verify workspace_id column is set correctly for each agent
    conn = sqlite3.connect(str(k.db_path))
    for agent_name in ws1_agents:
        row = conn.execute(
            "SELECT workspace_id FROM agent_status WHERE agent_name = ?", (agent_name,)
        ).fetchone()
        assert row and row[0] == ws1, f"{agent_name}: expected ws={ws1}, got {row}"
    for agent_name in ws2_agents:
        row = conn.execute(
            "SELECT workspace_id FROM agent_status WHERE agent_name = ?", (agent_name,)
        ).fetchone()
        assert row and row[0] == ws2, f"{agent_name}: expected ws={ws2}, got {row}"
    conn.close()
    ok("workspace_id column correctly set in agent_status for all agents")
except Exception as e:
    fail("workspace_id column verification", e)

# ══════════════════════════════════════════════════════════════════════
# list_workspaces
# ══════════════════════════════════════════════════════════════════════
print("\n--- list_workspaces ---")

try:
    workspaces = k.list_workspaces()
    active = [w for w in workspaces if w["status"] == "active"]
    ws_ids = [w["id"] for w in active]
    assert ws1 in ws_ids and ws2 in ws_ids, f"both workspaces should appear: {ws_ids}"
    ok(f"list_workspaces: {len(active)} active workspaces (includes both launches)")
except Exception as e:
    fail("list_workspaces", e)

try:
    workspaces = k.list_workspaces()
    w1_detail = next(w for w in workspaces if w["id"] == ws1)
    w2_detail = next(w for w in workspaces if w["id"] == ws2)
    assert w1_detail["agent_count"] == 5
    assert w2_detail["agent_count"] == 3
    ok(f"list_workspaces agent_count: ws1={w1_detail['agent_count']}, ws2={w2_detail['agent_count']}")
except Exception as e:
    fail("list_workspaces agent counts", e)

# ══════════════════════════════════════════════════════════════════════
# Idempotency — same workspace_id, second launch skips existing agents
# ══════════════════════════════════════════════════════════════════════
print("\n--- Idempotency ---")

try:
    r_repeat = k.launch_template("startup-chief-of-staff", workspace_id=ws1)
    assert r_repeat["workspace_id"] == ws1
    assert r_repeat["agents_created"] == [], \
        f"second launch to same workspace should create 0 agents, got {r_repeat['agents_created']}"
    ok("launch_template is idempotent for same workspace_id")
except Exception as e:
    fail("idempotency same workspace", e)

# ══════════════════════════════════════════════════════════════════════
# route_model resolves workspace-namespaced agent config
# ══════════════════════════════════════════════════════════════════════
print("\n--- route_model with workspace agent ---")

try:
    model = k.route_model(f"{ws1}-scout")
    assert model, "route_model should return a non-empty string"
    ok(f"route_model resolves {ws1}-scout → {model}")
except Exception as e:
    fail("route_model workspace agent", e)

try:
    model = k.route_model(f"{ws2}-analyst")
    assert model
    ok(f"route_model resolves {ws2}-analyst → {model}")
except Exception as e:
    fail("route_model research-assistant analyst", e)

# ── Summary ─────────────────────────────────────────────────────────
print(f"\n{'='*50}")
print(f"  Tests passed: {passed}")
print(f"  Tests failed: {failed}")
print(f"{'='*50}")

if failed > 0:
    sys.exit(1)
PYEOF

echo ""
echo "=== Template + Workspace Tests Complete ==="
