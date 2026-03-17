"""
User preferences store for APEX Telegram bot.

Manages per-workspace preferences: preferred sources, voice samples, platform target.
All data lives in the user_preferences table in the shared SQLite DB.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

DEFAULT_SOURCES: list[str] = [
    "arxiv.org",
    "github.com",
    "mckinsey.com",
    "bain.com",
    "economist.com",
]

PLATFORM_INSTRUCTIONS: dict[str, str] = {
    "linkedin": (
        "Write a 1000-1500 character thought leadership post. "
        "Lead with a contrarian or data-backed hook. "
        "Use short paragraphs. End with a question or CTA. 3-5 hashtags."
    ),
    "x": (
        "Write a punchy tweet under 280 characters. "
        "Alternatively write a 5-7 tweet thread with a hook first tweet. "
        "Be provocative and shareable."
    ),
    "tiktok": (
        "Write a 15-30 second video script. "
        "Hook in the first line (must grab attention in 0.5 seconds). "
        "Casual educational tone. Include direction for visuals in brackets. "
        "End with a follow CTA."
    ),
    "instagram": (
        "Write a caption under 300 characters with a visual-first hook. "
        "Include 5-10 hashtags. Suggest image/carousel concept."
    ),
}

VALID_PLATFORMS = set(PLATFORM_INSTRUCTIONS.keys())


class UserPreferencesStore:
    """Thin wrapper around the user_preferences SQLite table."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._ensure_table()

    # ── Internal helpers ────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    workspace_id TEXT NOT NULL,
                    preference_type TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(workspace_id, preference_type, key)
                )
            """)
            conn.commit()

    # ── Generic CRUD ────────────────────────────────────────────────────────

    def set_pref(self, workspace_id: str, preference_type: str, key: str, value: Any) -> None:
        val_str = value if isinstance(value, str) else json.dumps(value)
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO user_preferences (workspace_id, preference_type, key, value, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(workspace_id, preference_type, key)
                DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (workspace_id, preference_type, key, val_str))
            conn.commit()

    def get_pref(self, workspace_id: str, preference_type: str, key: str) -> Any:
        with self._connect() as conn:
            row = conn.execute("""
                SELECT value FROM user_preferences
                WHERE workspace_id = ? AND preference_type = ? AND key = ?
            """, (workspace_id, preference_type, key)).fetchone()
        if row is None:
            return None
        raw = row["value"]
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw

    def get_all_prefs(self, workspace_id: str, preference_type: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT key, value, updated_at FROM user_preferences
                WHERE workspace_id = ? AND preference_type = ?
                ORDER BY updated_at ASC
            """, (workspace_id, preference_type)).fetchall()
        result = []
        for row in rows:
            raw = row["value"]
            try:
                val: Any = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                val = raw
            result.append({"key": row["key"], "value": val, "updated_at": row["updated_at"]})
        return result

    def delete_pref(self, workspace_id: str, preference_type: str, key: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("""
                DELETE FROM user_preferences
                WHERE workspace_id = ? AND preference_type = ? AND key = ?
            """, (workspace_id, preference_type, key))
            conn.commit()
        return cur.rowcount > 0

    def clear_prefs(self, workspace_id: str, preference_type: str) -> int:
        with self._connect() as conn:
            cur = conn.execute("""
                DELETE FROM user_preferences
                WHERE workspace_id = ? AND preference_type = ?
            """, (workspace_id, preference_type))
            conn.commit()
        return cur.rowcount

    # ── Domain-specific accessors ────────────────────────────────────────────

    def get_sources(self, workspace_id: str) -> list[str]:
        """Return preferred source domains; falls back to defaults if none stored."""
        prefs = self.get_all_prefs(workspace_id, "preferred_source")
        if not prefs:
            return list(DEFAULT_SOURCES)
        return [str(p["value"]) for p in prefs]

    def add_source(self, workspace_id: str, domain: str) -> None:
        self.set_pref(workspace_id, "preferred_source", domain, domain)

    def remove_source(self, workspace_id: str, domain: str) -> bool:
        return self.delete_pref(workspace_id, "preferred_source", domain)

    def reset_sources(self, workspace_id: str) -> None:
        self.clear_prefs(workspace_id, "preferred_source")

    def get_voice_samples(self, workspace_id: str, platform: str) -> list[str]:
        """Return stored voice sample texts for a platform (up to 10)."""
        pref_type = f"voice_sample:{platform.lower().strip()}"
        prefs = self.get_all_prefs(workspace_id, pref_type)
        return [str(p["value"]) for p in prefs[:10]]

    def add_voice_sample(self, workspace_id: str, platform: str, text: str) -> int:
        """Add a voice sample tagged to a platform. Returns current count. Raises if at 10."""
        pref_type = f"voice_sample:{platform.lower().strip()}"
        existing = self.get_all_prefs(workspace_id, pref_type)
        if len(existing) >= 10:
            raise ValueError(f"Maximum 10 voice samples reached for {platform}. Clear some first.")
        key = f"sample_{len(existing) + 1}"
        self.set_pref(workspace_id, pref_type, key, text)
        return len(existing) + 1

    def clear_voice_samples(self, workspace_id: str, platform: str) -> int:
        """Clear all voice samples for a specific platform."""
        pref_type = f"voice_sample:{platform.lower().strip()}"
        return self.clear_prefs(workspace_id, pref_type)

    def get_voice_sample_counts(self, workspace_id: str) -> dict[str, int]:
        """Return {platform: count} for all valid platforms."""
        return {
            p: len(self.get_all_prefs(workspace_id, f"voice_sample:{p}"))
            for p in sorted(VALID_PLATFORMS)
        }

    def get_platform(self, workspace_id: str) -> str | None:
        """Return the target platform string (e.g. 'linkedin') or None."""
        return self.get_pref(workspace_id, "platform", "target")

    def set_platform(self, workspace_id: str, platform: str) -> None:
        self.set_pref(workspace_id, "platform", "target", platform)

    # ── X (Twitter) credentials ──────────────────────────────────────────────

    def set_x_credentials(
        self,
        workspace_id: str,
        api_key: str,
        api_secret: str,
        access_token: str,
        access_secret: str,
    ) -> None:
        """Store X OAuth 1.0a credentials for this workspace."""
        for key, val in [
            ("api_key", api_key),
            ("api_secret", api_secret),
            ("access_token", access_token),
            ("access_secret", access_secret),
        ]:
            self.set_pref(workspace_id, "x_credentials", key, val)

    def get_x_credentials(self, workspace_id: str) -> dict[str, str] | None:
        """Return X credentials dict, or None if any key is missing."""
        creds: dict[str, str] = {}
        for key in ("api_key", "api_secret", "access_token", "access_secret"):
            val = self.get_pref(workspace_id, "x_credentials", key)
            if not val:
                return None
            creds[key] = str(val)
        return creds

    def clear_x_credentials(self, workspace_id: str) -> None:
        """Remove stored X credentials for this workspace."""
        self.clear_prefs(workspace_id, "x_credentials")

    # ── Workspace friendly names ─────────────────────────────────────────────

    # Stored under a fixed sentinel workspace_id so lookups are global.
    _WS_NAMES_SCOPE = "__workspace_names__"

    def set_workspace_name(self, workspace_id: str, name: str) -> None:
        """Store a human-friendly name for a workspace ID."""
        # Forward: ws-id → name
        self.set_pref(self._WS_NAMES_SCOPE, "ws_name", workspace_id, name)
        # Reverse: normalised-name → ws-id (for lookup by name in /task)
        self.set_pref(self._WS_NAMES_SCOPE, "ws_id_by_name", name.lower().strip(), workspace_id)

    def get_workspace_name(self, workspace_id: str) -> str | None:
        """Return the friendly name for a workspace ID, or None."""
        val = self.get_pref(self._WS_NAMES_SCOPE, "ws_name", workspace_id)
        return str(val) if val else None

    def resolve_workspace_id(self, name_or_id: str) -> str:
        """
        Given either a workspace ID (ws-...) or a friendly name, return the
        canonical workspace ID.  Returns the input unchanged if it looks like
        a raw ID or if no matching name is found.
        """
        candidate = name_or_id.strip()
        # Already looks like a raw ID
        if candidate.startswith("ws-"):
            return candidate
        # Try case-insensitive name → id lookup
        val = self.get_pref(self._WS_NAMES_SCOPE, "ws_id_by_name", candidate.lower())
        return str(val) if val else candidate
