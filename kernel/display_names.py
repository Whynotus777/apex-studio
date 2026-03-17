"""
Display name resolver for APEX agents.

Resolves human-friendly names for workspace-scoped agent names like
'ws-82c085cf-scout' → 'Scout' or 'Research Specialist'.

Resolution order:
1. Parse the role suffix from the agent name (everything after the last '-')
2. Look up the workspace's template to find the agent's description
3. Use the agent.json 'role' or 'name' field if available
4. Fall back to capitalizing the suffix: 'scout' → 'Scout'
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


_ICON_MAP: dict[str, str] = {
    "scout": "🔭",
    "analyst": "📊",
    "writer": "✍️",
    "critic": "🛡️",
    "strategist": "🎯",
    "scheduler": "📅",
    "operator": "📡",
    "researcher": "🔬",
    "editor": "✏️",
    "publisher": "📢",
}

_DEFAULT_ICON = "🤖"


class DisplayNameResolver:
    """Resolves friendly display names for workspace-scoped agent names."""

    def __init__(self, apex_home: str | Path | None = None, db_path: str | Path | None = None) -> None:
        self.apex_home = Path(apex_home or Path(__file__).resolve().parents[1]).resolve()
        self.db_path = Path(db_path or self.apex_home / "db" / "apex_state.db").resolve()
        self._cache: dict[str, dict[str, Any]] = {}  # workspace_id → {local_name → info dict}

    # ── Public API ──────────────────────────────────────────────────────────

    def get_display_name(self, agent_name: str, workspace_id: str | None = None) -> str:
        """Return a friendly display name for an agent.

        Args:
            agent_name: Full agent name, e.g. 'ws-82c085cf-scout' or 'scout'.
            workspace_id: Optional workspace context; inferred from agent_name if omitted.

        Returns:
            Human-readable name, e.g. 'Research Scout' or 'Scout'.
        """
        info = self.get_display_info(agent_name, workspace_id)
        return info["display_name"]

    def get_display_info(self, agent_name: str, workspace_id: str | None = None) -> dict[str, Any]:
        """Return full display metadata for an agent.

        Returns dict with keys:
            internal_name  – the original agent_name passed in
            display_name   – human-readable label
            role_description – longer description string or None
            icon           – emoji icon character
        """
        ws_id, local_name = self._parse_agent_name(agent_name, workspace_id)

        if ws_id:
            cache = self._get_workspace_cache(ws_id)
            if local_name in cache:
                entry = cache[local_name]
                return {
                    "internal_name": agent_name,
                    "display_name": entry["display_name"],
                    "role_description": entry.get("role_description"),
                    "icon": entry.get("icon", _DEFAULT_ICON),
                }

        # No workspace or agent not in cache — fall back to suffix capitalisation
        return {
            "internal_name": agent_name,
            "display_name": _suffix_to_display(local_name),
            "role_description": None,
            "icon": _ICON_MAP.get(local_name.lower(), _DEFAULT_ICON),
        }

    def get_team_display_names(self, workspace_id: str) -> list[dict[str, Any]]:
        """Return display info for every agent in a workspace.

        Returns list of dicts (same shape as get_display_info) sorted by local name.
        """
        cache = self._get_workspace_cache(workspace_id)
        result = []
        for local_name, entry in sorted(cache.items()):
            result.append({
                "internal_name": entry.get("internal_name", local_name),
                "display_name": entry["display_name"],
                "role_description": entry.get("role_description"),
                "icon": entry.get("icon", _DEFAULT_ICON),
            })
        return result

    def get_critic_display_name(self, workspace_id: str) -> str:
        """Return the display name for the critic agent in a workspace.

        Falls back to 'Quality Editor' if no critic agent is found.
        """
        cache = self._get_workspace_cache(workspace_id)
        for local_name, entry in cache.items():
            if local_name == "critic" or entry.get("role", "").lower() == "quality_gate":
                return entry["display_name"]
        return "Quality Editor"

    # ── Cache management ────────────────────────────────────────────────────

    def _get_workspace_cache(self, workspace_id: str) -> dict[str, Any]:
        """Return (possibly cached) agent info dict for a workspace."""
        if workspace_id in self._cache:
            return self._cache[workspace_id]

        info = self._build_workspace_cache(workspace_id)
        self._cache[workspace_id] = info
        return info

    def _build_workspace_cache(self, workspace_id: str) -> dict[str, Any]:
        """Build agent info dict by reading template files."""
        template_id = self._get_workspace_template(workspace_id)
        if not template_id:
            return {}

        agents_dir = self.apex_home / "templates" / template_id / "agents"
        if not agents_dir.is_dir():
            return {}

        cache: dict[str, Any] = {}
        for agent_dir in agents_dir.iterdir():
            if not agent_dir.is_dir():
                continue
            local_name = agent_dir.name
            agent_json_path = agent_dir / "agent.json"
            agent_data = _read_json_safe(agent_json_path)

            display_name = _extract_display_name(agent_data, local_name)
            role_description = _extract_role_description(agent_data)
            icon = _ICON_MAP.get(local_name.lower(), _DEFAULT_ICON)

            cache[local_name] = {
                "internal_name": f"{workspace_id}-{local_name}",
                "display_name": display_name,
                "role_description": role_description,
                "icon": icon,
                "role": agent_data.get("role", ""),
            }

        return cache

    def _get_workspace_template(self, workspace_id: str) -> str | None:
        """Look up template_id for a workspace from the DB."""
        try:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    "SELECT template_id FROM workspaces WHERE id = ?", (workspace_id,)
                ).fetchone()
                return row["template_id"] if row else None
            finally:
                conn.close()
        except Exception:
            return None

    # ── Name parsing ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_agent_name(agent_name: str, workspace_id: str | None) -> tuple[str | None, str]:
        """Split 'ws-82c085cf-scout' → ('ws-82c085cf', 'scout').

        Returns (workspace_id_or_None, local_name).
        """
        parts = agent_name.split("-")
        # Workspace-scoped format: ws-{8hex}-{name}
        if len(parts) >= 3 and parts[0] == "ws":
            ws = "-".join(parts[:2])
            local = "-".join(parts[2:])
            return workspace_id or ws, local
        # Bare local name
        return workspace_id, agent_name


# ── Module-level helpers ────────────────────────────────────────────────────

def _suffix_to_display(local_name: str) -> str:
    """'scout' → 'Scout', 'chief-of-staff' → 'Chief Of Staff'."""
    return " ".join(word.capitalize() for word in local_name.replace("-", " ").split())


def _extract_display_name(agent_data: dict[str, Any], local_name: str) -> str:
    """Extract a friendly display name from agent.json data.

    Checks (in order):
    1. description field — if it contains ' — ', take the part before it
       e.g. "Research Scout — finds sources..." → "Research Scout"
    2. Fall back to capitalizing the local_name suffix
       e.g. "scout" → "Scout"

    The description field alone (without em-dash) is treated as role_description
    only, not as a display name.
    """
    description = agent_data.get("description", "")
    if description and " — " in description:
        before_dash = description.split(" — ")[0].strip()
        if before_dash:
            return before_dash

    return _suffix_to_display(local_name)


def _extract_role_description(agent_data: dict[str, Any]) -> str | None:
    """Extract the role description from agent.json data.

    Uses:
    1. Part after ' — ' in description (if em-dash present)
    2. Full description field (if no em-dash)
    """
    description = agent_data.get("description", "")
    if not description:
        return None
    if " — " in description:
        after_dash = description.split(" — ", 1)[1].strip()
        return after_dash or None
    return description.strip() or None


def _read_json_safe(path: Path) -> dict[str, Any]:
    """Read a JSON file, returning {} on any error."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
