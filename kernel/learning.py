from __future__ import annotations

import json
import sqlite3
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


_DEFAULT_PLATFORM_PROFILES: dict[str, dict[str, str]] = {
    "linkedin": {
        "format_rules": (
            "Open with a sharp professional hook, deliver 2-5 concrete insights, "
            "favor line breaks over long paragraphs, and end with a discussion prompt or CTA."
        ),
        "tone": "credible, specific, operator-minded, insightful",
        "optimization_mode": "authority",
    },
    "x": {
        "format_rules": (
            "Lead with a punchy hook, compress ideas aggressively, use short lines, and optimize for shareability."
        ),
        "tone": "fast, opinionated, clear, high-signal",
        "optimization_mode": "virality",
    },
    "tiktok": {
        "format_rules": (
            "Front-load the first 2 seconds, keep language spoken and visual, and structure for retention beats."
        ),
        "tone": "energetic, concrete, conversational, visually driven",
        "optimization_mode": "watch_time",
    },
    "instagram": {
        "format_rules": (
            "Start with a visually resonant hook, keep captions scannable, and design for saves and shares."
        ),
        "tone": "aspirational, clear, polished, relatable",
        "optimization_mode": "saves",
    },
}


_OPTIMIZATION_GUIDANCE: dict[str, dict[str, str]] = {
    "linkedin": {
        "authority": "optimize for depth of insight, credibility, and decision-maker trust",
        "virality": "optimize for strong hooks, contrarian angles, and repost-worthy framing",
        "lead_gen": "optimize for pain-point clarity, CTA quality, and conversion intent",
        "network_growth": "optimize for comments, conversation, and relationship-building",
    },
    "x": {
        "virality": "optimize for hooks, novelty, and repost potential",
        "engagement": "optimize for replies, quote tweets, and easy reaction surfaces",
        "followers": "optimize for identity signaling and high-signal repeatability",
    },
    "tiktok": {
        "watch_time": "optimize for retention, pacing, and curiosity loops",
        "shares": "optimize for surprise, utility, and instantly explainable value",
        "followers": "optimize for recurring series potential and creator identity",
    },
    "instagram": {
        "saves": "optimize for educational density and evergreen usefulness",
        "reach": "optimize for immediate clarity, broad relevance, and hook strength",
        "engagement": "optimize for comments, DMs, and emotional resonance",
    },
}


class AgentLearning:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._migrate()

    def set_preference(self, workspace_id: str, pref_type: str, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences (id, workspace_id, preference_type, key, value, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(workspace_id, preference_type, key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = datetime('now')
                """,
                (f"pref-{uuid.uuid4().hex[:12]}", workspace_id, pref_type, key, value),
            )
            conn.commit()

    def get_preferences(self, workspace_id: str, pref_type: str) -> list[dict[str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT key, value
                FROM user_preferences
                WHERE workspace_id = ? AND preference_type = ?
                ORDER BY updated_at DESC, key ASC
                """,
                (workspace_id, pref_type),
            ).fetchall()
        return [{"key": str(row["key"]), "value": str(row["value"])} for row in rows]

    def add_voice_sample(self, workspace_id: str, platform: str, sample_text: str) -> None:
        platform_key = platform.lower().strip()
        sample_text = sample_text.strip()
        if not sample_text:
            return
        pref_type = f"voice_sample:{platform_key}"
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT id FROM user_preferences
                WHERE workspace_id = ? AND preference_type = ?
                ORDER BY rowid DESC
                """,
                (workspace_id, pref_type),
            ).fetchall()
            conn.execute(
                """
                INSERT INTO user_preferences (id, workspace_id, preference_type, key, value, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    f"pref-{uuid.uuid4().hex[:12]}",
                    workspace_id,
                    pref_type,
                    f"sample-{uuid.uuid4().hex[:8]}",
                    sample_text,
                ),
            )
            if len(existing) >= 10:
                stale_ids = [row["id"] for row in existing[9:]]
                placeholders = ",".join("?" for _ in stale_ids)
                conn.execute(
                    f"DELETE FROM user_preferences WHERE id IN ({placeholders})",
                    stale_ids,
                )
            conn.commit()

    def get_voice_samples(self, workspace_id: str, platform: str) -> list[str]:
        pref_type = f"voice_sample:{platform.lower().strip()}"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT value
                FROM user_preferences
                WHERE workspace_id = ? AND preference_type = ?
                ORDER BY rowid DESC
                LIMIT 10
                """,
                (workspace_id, pref_type),
            ).fetchall()
        return [str(row["value"]) for row in rows]

    def set_platform_profile(
        self,
        workspace_id: str,
        platform: str,
        format_rules: str,
        tone: str,
        optimization_mode: str,
    ) -> None:
        platform_key = platform.lower().strip()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO platform_profiles (
                    id, workspace_id, platform, format_rules, tone, optimization_mode, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(workspace_id, platform) DO UPDATE SET
                    format_rules = excluded.format_rules,
                    tone = excluded.tone,
                    optimization_mode = excluded.optimization_mode,
                    updated_at = datetime('now')
                """,
                (
                    f"profile-{uuid.uuid4().hex[:12]}",
                    workspace_id,
                    platform_key,
                    format_rules,
                    tone,
                    optimization_mode,
                ),
            )
            conn.commit()

    def get_platform_profile(self, workspace_id: str, platform: str) -> dict[str, str]:
        platform_key = platform.lower().strip()
        profile = dict(_DEFAULT_PLATFORM_PROFILES.get(platform_key, {
            "format_rules": "Adapt to the platform's native format.",
            "tone": "clear, useful, audience-aware",
            "optimization_mode": "engagement",
        }))
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT format_rules, tone, optimization_mode
                FROM platform_profiles
                WHERE workspace_id = ? AND platform = ?
                """,
                (workspace_id, platform_key),
            ).fetchone()
        if row is not None:
            profile.update({
                "format_rules": str(row["format_rules"]),
                "tone": str(row["tone"]),
                "optimization_mode": str(row["optimization_mode"]),
            })
        return profile

    def record_performance(
        self,
        workspace_id: str,
        task_id: str,
        platform: str,
        metrics: dict[str, Any],
    ) -> str:
        record_id = f"perf-{uuid.uuid4().hex[:12]}"
        payload = dict(metrics)
        if "engagement_score" not in payload:
            payload["engagement_score"] = self._compute_engagement_score(payload)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO performance_records (id, workspace_id, task_id, platform, metrics_json, recorded_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                """,
                (record_id, workspace_id, task_id, platform.lower().strip(), json.dumps(payload)),
            )
            conn.commit()
        return record_id

    def get_performance_history(self, workspace_id: str, platform: str, n: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, workspace_id, task_id, platform, metrics_json, recorded_at
                FROM performance_records
                WHERE workspace_id = ? AND platform = ?
                ORDER BY recorded_at DESC, id DESC
                LIMIT ?
                """,
                (workspace_id, platform.lower().strip(), n),
            ).fetchall()
        return [self._performance_row_to_dict(row) for row in rows]

    def get_top_patterns(self, workspace_id: str, platform: str, n: int = 5) -> dict[str, list[str]]:
        history = self.get_performance_history(workspace_id, platform, n=200)
        if not history:
            return {
                "topic_keywords": [],
                "structure_type": [],
                "hook_style": [],
                "source_domains": [],
                "post_length": [],
                "time_posted": [],
            }

        counters: dict[str, Counter[str]] = {
            "topic_keywords": Counter(),
            "structure_type": Counter(),
            "hook_style": Counter(),
            "source_domains": Counter(),
            "post_length": Counter(),
            "time_posted": Counter(),
        }
        for record in history:
            metrics = record["metrics"]
            weight = float(metrics.get("engagement_score") or self._compute_engagement_score(metrics))
            self._add_pattern_values(counters["topic_keywords"], metrics.get("topic_keywords"), weight)
            self._add_pattern_values(counters["structure_type"], metrics.get("structure_type"), weight)
            self._add_pattern_values(counters["hook_style"], metrics.get("hook_style"), weight)
            self._add_pattern_values(counters["source_domains"], metrics.get("source_domains"), weight)
            self._add_pattern_values(counters["post_length"], metrics.get("post_length"), weight)
            self._add_pattern_values(counters["time_posted"], metrics.get("time_posted"), weight)

        return {
            category: [item for item, _score in counter.most_common(n)]
            for category, counter in counters.items()
        }

    def format_for_scout(self, workspace_id: str) -> str:
        source_prefs = self.get_preferences(workspace_id, "source_preference")
        domain_prefs = self.get_preferences(workspace_id, "preferred_domain")
        lines = ["## Learned Search Preferences"]
        if source_prefs:
            lines.append("Preferred source types:")
            for pref in source_prefs:
                lines.append(f"- {pref['key']}: {pref['value']}")
        else:
            lines.append("Preferred source types: none recorded")
        if domain_prefs:
            domains = ", ".join(pref["value"] for pref in domain_prefs)
            lines.append(f"Preferred domains/search filters: {domains}")
        else:
            lines.append("Preferred domains/search filters: none recorded")
        return "\n".join(lines)

    def format_for_writer(self, workspace_id: str, platform: str) -> str:
        profile = self.get_platform_profile(workspace_id, platform)
        style_prefs = self.get_preferences(workspace_id, "style_preference")
        voice_samples = self.get_voice_samples(workspace_id, platform)
        patterns = self.get_top_patterns(workspace_id, platform)
        lines = [f"## Writer Learning Context — {platform.lower()}"]
        lines.append(f"Platform format rules: {profile['format_rules']}")
        lines.append(f"Tone guidance: {profile['tone']}")
        lines.append(f"Optimization mode: {profile['optimization_mode']}")
        if style_prefs:
            lines.append("Style preferences:")
            for pref in style_prefs:
                lines.append(f"- {pref['key']}: {pref['value']}")
        if voice_samples:
            lines.append("Voice samples:")
            for idx, sample in enumerate(voice_samples[:3], start=1):
                lines.append(f"- Sample {idx}: {sample[:240]}")
        else:
            lines.append("Voice samples: none recorded")
        lines.append("Top-performing patterns:")
        for category, values in patterns.items():
            joined = ", ".join(values) if values else "none yet"
            lines.append(f"- {category}: {joined}")
        guidance = _OPTIMIZATION_GUIDANCE.get(platform.lower().strip(), {}).get(
            profile["optimization_mode"],
            "optimize for audience response and clarity",
        )
        lines.append(f"Optimization guidance: {guidance}")
        return "\n".join(lines)

    def format_for_critic(self, workspace_id: str, platform: str) -> str:
        profile = self.get_platform_profile(workspace_id, platform)
        guidance = _OPTIMIZATION_GUIDANCE.get(platform.lower().strip(), {}).get(
            profile["optimization_mode"],
            "optimize for clarity, usefulness, and audience response",
        )
        scoring_adjustments = {
            "linkedin": "Weigh depth of insight, credibility, and claim support higher.",
            "x": "Weigh hook strength, compression, and repost potential higher.",
            "tiktok": "Weigh hook strength, retention, and watch-time design higher.",
            "instagram": "Weigh clarity, save-worthiness, and visual resonance higher.",
        }.get(platform.lower().strip(), "Adjust scoring toward platform-native performance while preserving grounding.")
        return "\n".join([
            f"## Critic Learning Context — {platform.lower()}",
            f"Optimization target: {profile['optimization_mode']}",
            f"Metric guidance: {guidance}",
            f"Scoring adjustment: {scoring_adjustments}",
            "Keep evidence grounding and claim accuracy mandatory regardless of platform.",
        ])

    def _migrate(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT,
                    preference_type TEXT,
                    key TEXT,
                    value TEXT,
                    updated_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(workspace_id, preference_type, key)
                );

                CREATE TABLE IF NOT EXISTS performance_records (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT,
                    task_id TEXT,
                    platform TEXT,
                    metrics_json TEXT,
                    recorded_at TEXT DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS platform_profiles (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT,
                    platform TEXT,
                    format_rules TEXT,
                    tone TEXT,
                    optimization_mode TEXT,
                    updated_at TEXT DEFAULT (datetime('now')),
                    UNIQUE(workspace_id, platform)
                );
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _performance_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        return {
            "id": row["id"],
            "workspace_id": row["workspace_id"],
            "task_id": row["task_id"],
            "platform": row["platform"],
            "metrics": metrics,
            "recorded_at": row["recorded_at"],
        }

    def _compute_engagement_score(self, metrics: dict[str, Any]) -> float:
        impressions = max(float(metrics.get("impressions", 0) or 0), 1.0)
        likes = float(metrics.get("likes", 0) or 0)
        comments = float(metrics.get("comments", 0) or 0)
        reposts = float(metrics.get("reposts", 0) or 0)
        shares = float(metrics.get("shares", 0) or 0)
        follows = float(metrics.get("follows", 0) or 0)
        watch_time = float(metrics.get("watch_time", 0) or 0)
        return round(
            ((likes + comments * 2 + reposts * 3 + shares * 3 + follows * 4) / impressions) * 100
            + (watch_time / impressions),
            4,
        )

    def _add_pattern_values(self, counter: Counter[str], value: Any, weight: float) -> None:
        if value is None:
            return
        if isinstance(value, str):
            counter[value] += weight
            return
        if isinstance(value, list):
            for item in value:
                if item:
                    counter[str(item)] += weight
