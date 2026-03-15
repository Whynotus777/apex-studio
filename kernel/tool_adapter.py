from __future__ import annotations

from typing import Any, Callable

from adapters.tools.web_search import search as web_search


ToolFunction = Callable[..., Any]


TOOL_REGISTRY: dict[str, ToolFunction] = {
    "web_search": web_search,
}


def execute_tool(tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a registered tool and return structured results."""
    params = params or {}
    if tool_name not in TOOL_REGISTRY:
        raise ValueError(f"Unknown tool: {tool_name}")

    result = TOOL_REGISTRY[tool_name](**params)
    if tool_name == "web_search":
        return {
            "tool": tool_name,
            "status": "ok",
            "count": len(result),
            "results": result,
            "evidence": result,
        }

    return {
        "tool": tool_name,
        "status": "ok",
        "results": result,
    }
