#!/bin/bash
# test_primitives.sh — Phase B primitive tests (Tool, Permission, Budget)
# Pure Python via kernel/api.py — no model calls, runs in seconds.
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

echo "=== APEX Phase B Primitive Tests ==="
echo ""

python3 - <<'PYEOF'
import sys, sqlite3
sys.path.insert(0, ".")
from kernel.api import ApexKernel

k = ApexKernel()

# ── Cleanup any leftover test data from a prior run ───────────────────
def cleanup(conn):
    conn.execute("DELETE FROM spend_log   WHERE agent_id='builder' AND description LIKE 'prim-test%'")
    conn.execute("DELETE FROM budgets     WHERE agent_id='builder' AND budget_type  LIKE 'prim-test%'")
    conn.execute("DELETE FROM permissions WHERE agent_id='builder' AND resource     LIKE 'prim-test%'")
    conn.execute("DELETE FROM tool_grants WHERE tool_id LIKE 'prim-test%'")
    conn.execute("DELETE FROM tools       WHERE id       LIKE 'prim-test%'")
    conn.commit()

conn = sqlite3.connect(str(k.db_path))
cleanup(conn)
# Ensure 'builder' exists in agent_status (tests may run in isolation)
conn.execute(
    "INSERT OR IGNORE INTO agent_status (agent_name, status, model_active) VALUES ('builder', 'idle', 'qwen3.5-apex')"
)
conn.commit()
conn.close()

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

# ══════════════════════════════════════════════════════════════════════
# TOOL PRIMITIVE
# ══════════════════════════════════════════════════════════════════════
print("--- Tool Primitive ---")

# register_tool
try:
    tid = k.register_tool({
        "id": "prim-test-search",
        "name": "Search (prim-test)",
        "adapter": "perplexica",
        "auth_method": "api_key",
        "scopes": ["search"],
        "read_write": "read",
        "cost_per_call": 0.002,
        "approval_required": False,
    })
    assert tid == "prim-test-search"
    ok("register_tool creates tool with correct id")
except Exception as e:
    fail("register_tool", e)

# register_tool — duplicate should fail
try:
    k.register_tool({"id": "prim-test-search", "name": "dup"})
    fail("register_tool duplicate should raise")
except Exception:
    ok("register_tool duplicate raises correctly")

# grant_tool_access
try:
    k.grant_tool_access("builder", "prim-test-search", "read_only")
    ok("grant_tool_access read_only to builder")
except Exception as e:
    fail("grant_tool_access", e)

# invalid level
try:
    k.grant_tool_access("builder", "prim-test-search", "superuser")
    fail("grant_tool_access invalid level should raise")
except ValueError:
    ok("grant_tool_access rejects invalid level")

# get_agent_tools
try:
    tools = k.get_agent_tools("builder")
    assert any(t["tool_id"] == "prim-test-search" for t in tools)
    ok("get_agent_tools returns granted tool")
except Exception as e:
    fail("get_agent_tools", e)

# invoke_tool — authorized read
try:
    result = k.invoke_tool("builder", "prim-test-search", {"query": "test"})
    assert result["status"] == "authorized"
    assert result["permission_level"] == "read_only"
    ok("invoke_tool authorized (read_only + read tool)")
except Exception as e:
    fail("invoke_tool authorized", e)

# invoke_tool — no grant → PermissionError
try:
    k.invoke_tool("scout", "prim-test-search", {})
    fail("invoke_tool with no grant should raise PermissionError")
except PermissionError:
    ok("invoke_tool raises PermissionError when no grant")
except Exception as e:
    fail("invoke_tool no grant unexpected error", e)

# invoke_tool — read_only trying to use a write tool
try:
    k.register_tool({"id": "prim-test-write", "name": "WriteTest", "read_write": "write", "cost_per_call": 0})
    k.grant_tool_access("builder", "prim-test-write", "read_only")
    k.invoke_tool("builder", "prim-test-write", {})
    fail("invoke_tool read_only on write tool should raise PermissionError")
except PermissionError:
    ok("invoke_tool blocks read_only agent from write tool")
except Exception as e:
    fail("invoke_tool write block unexpected error", e)

# invoke_tool — approval_required with draft level → PermissionError
try:
    k.register_tool({"id": "prim-test-approval", "name": "ApprovalTest", "read_write": "write", "approval_required": True, "cost_per_call": 0})
    k.grant_tool_access("builder", "prim-test-approval", "draft")
    k.invoke_tool("builder", "prim-test-approval", {})
    fail("invoke_tool draft on approval_required tool should raise PermissionError")
except PermissionError:
    ok("invoke_tool blocks draft agent from approval_required tool")
except Exception as e:
    fail("invoke_tool approval block unexpected error", e)

# invoke_tool — write_with_approval level passes approval_required check
try:
    k.grant_tool_access("builder", "prim-test-approval", "write_with_approval")
    result = k.invoke_tool("builder", "prim-test-approval", {})
    assert result["status"] == "authorized"
    ok("invoke_tool write_with_approval passes approval_required check")
except Exception as e:
    fail("invoke_tool write_with_approval", e)

# revoke_tool_access
try:
    k.revoke_tool_access("builder", "prim-test-search")
    tools = k.get_agent_tools("builder")
    assert not any(t["tool_id"] == "prim-test-search" for t in tools)
    ok("revoke_tool_access removes grant")
except Exception as e:
    fail("revoke_tool_access", e)

# ══════════════════════════════════════════════════════════════════════
# PERMISSION PRIMITIVE
# ══════════════════════════════════════════════════════════════════════
print("\n--- Permission Primitive ---")

# set_permission and check_permission — allowed
try:
    k.set_permission("builder", "prim-test-web", "read_only", max_spend_per_day=5.0, requires_approval=False)
    result = k.check_permission("builder", "prim-test-web", "read")
    assert result == "allowed", f"got {result}"
    ok("check_permission read on read_only → allowed")
except Exception as e:
    fail("check_permission allowed", e)

# check_permission write on read_only → denied
try:
    result = k.check_permission("builder", "prim-test-web", "write")
    assert result == "denied", f"got {result}"
    ok("check_permission write on read_only → denied")
except Exception as e:
    fail("check_permission denied", e)

# set_permission requires_approval → needs_approval
try:
    k.set_permission("builder", "prim-test-email", "full_write", requires_approval=True)
    result = k.check_permission("builder", "prim-test-email", "write")
    assert result == "needs_approval", f"got {result}"
    ok("check_permission with requires_approval → needs_approval")
except Exception as e:
    fail("check_permission needs_approval", e)

# nonexistent resource → denied
try:
    result = k.check_permission("builder", "prim-test-nonexistent", "read")
    assert result == "denied", f"got {result}"
    ok("check_permission nonexistent resource → denied")
except Exception as e:
    fail("check_permission nonexistent", e)

# set_permission upsert updates level
try:
    k.set_permission("builder", "prim-test-web", "full_write", requires_approval=False)
    result = k.check_permission("builder", "prim-test-web", "write")
    assert result == "allowed", f"got {result}"
    ok("set_permission upsert updates level correctly")
except Exception as e:
    fail("set_permission upsert", e)

# get_agent_permissions
try:
    perms = k.get_agent_permissions("builder")
    resources = [p["resource"] for p in perms]
    assert "prim-test-web" in resources
    assert "prim-test-email" in resources
    ok(f"get_agent_permissions returns all permissions ({len(perms)} total)")
except Exception as e:
    fail("get_agent_permissions", e)

# ══════════════════════════════════════════════════════════════════════
# BUDGET PRIMITIVE
# ══════════════════════════════════════════════════════════════════════
print("\n--- Budget Primitive ---")

# set_budget
try:
    k.set_budget("builder", "prim-test-tokens", limit_amount=100.0, period="daily", alert_threshold=0.8)
    ok("set_budget creates budget")
except Exception as e:
    fail("set_budget", e)

# check_budget — allowed (well under threshold)
try:
    result = k.check_budget("builder", "prim-test-tokens", 50.0)
    assert result == "allowed", f"got {result}"
    ok("check_budget 50/100 → allowed")
except Exception as e:
    fail("check_budget allowed", e)

# record_spend
try:
    k.record_spend("builder", "prim-test-tokens", 50.0, "prim-test initial spend")
    ok("record_spend logs 50 tokens")
except Exception as e:
    fail("record_spend", e)

# check_budget — warning (projected 50+30 = 80 = 80% threshold)
try:
    result = k.check_budget("builder", "prim-test-tokens", 30.0)
    assert result == "warning", f"got {result}"
    ok("check_budget projected 80/100 → warning")
except Exception as e:
    fail("check_budget warning", e)

# check_budget — denied (would exceed limit)
try:
    result = k.check_budget("builder", "prim-test-tokens", 51.0)
    assert result == "denied", f"got {result}"
    ok("check_budget projected 101/100 → denied")
except Exception as e:
    fail("check_budget denied", e)

# record_spend over limit → PermissionError
try:
    k.record_spend("builder", "prim-test-tokens", 51.0, "prim-test over limit")
    fail("record_spend over limit should raise PermissionError")
except PermissionError:
    ok("record_spend raises PermissionError when over limit")
except Exception as e:
    fail("record_spend over limit", e)

# get_budget_status — should be "ok" at 50%
try:
    statuses = k.get_budget_status("builder")
    b = next(b for b in statuses if b["budget_type"] == "prim-test-tokens")
    assert float(b["spent_amount"]) == 50.0, f"expected 50.0, got {b['spent_amount']}"
    assert b["status"] == "ok", f"expected ok at 50%, got {b['status']}"
    assert b["remaining"] == 50.0
    ok(f"get_budget_status: spent={b['spent_amount']}, remaining={b['remaining']}, status={b['status']}")
except Exception as e:
    fail("get_budget_status", e)

# record more to hit warning threshold, then re-check status
try:
    k.record_spend("builder", "prim-test-tokens", 30.0, "prim-test threshold spend")
    statuses = k.get_budget_status("builder")
    b = next(b for b in statuses if b["budget_type"] == "prim-test-tokens")
    assert b["status"] == "warning", f"expected warning at 80%, got {b['status']}"
    ok(f"get_budget_status after 80% spend → status={b['status']}")
except Exception as e:
    fail("get_budget_status warning", e)

# set_budget invalid params
try:
    k.set_budget("builder", "prim-test-bad", 0, "daily")
    fail("set_budget limit=0 should raise")
except ValueError:
    ok("set_budget rejects limit_amount=0")

try:
    k.set_budget("builder", "prim-test-bad", 100, "daily", alert_threshold=1.5)
    fail("set_budget alert_threshold>1 should raise")
except ValueError:
    ok("set_budget rejects alert_threshold > 1")

# check_budget nonexistent → ValueError
try:
    k.check_budget("builder", "prim-test-nonexistent", 1.0)
    fail("check_budget nonexistent should raise ValueError")
except ValueError:
    ok("check_budget raises ValueError for nonexistent budget")

# ── Summary ───────────────────────────────────────────────────────────
print(f"\n{'='*42}")
print(f"  Tests passed: {passed}")
print(f"  Tests failed: {failed}")
print(f"{'='*42}")

if failed > 0:
    sys.exit(1)
PYEOF

echo ""
echo "=== Primitive Tests Complete ==="
