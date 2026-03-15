#!/bin/bash
set -e
APEX_HOME="${APEX_HOME:-$HOME/apex-studio}"
[ -f "$APEX_HOME/.env" ] && export $(grep -v '^#' "$APEX_HOME/.env" | xargs)

echo "=== APEX Web Search Test ==="
echo ""

python3 - <<'PYEOF'
import sys

sys.path.insert(0, ".")

from kernel.tool_adapter import execute_tool

query = "AI private equity deal analysis"
result = execute_tool("web_search", {"query": query, "max_results": 5})

assert result["tool"] == "web_search", result
assert result["status"] == "ok", result
assert result["count"] > 0, result
assert isinstance(result["results"], list), result

first = result["results"][0]
for field in ("title", "url", "snippet", "source"):
    assert field in first, (field, first)
    assert first[field], (field, first)

print(f"Query: {query}")
print(f"Results: {result['count']}")
print("First result:")
print(f"  title: {first['title']}")
print(f"  url: {first['url']}")
print(f"  snippet: {first['snippet']}")
print("")
print("PASS web_search returned structured evidence")
PYEOF
