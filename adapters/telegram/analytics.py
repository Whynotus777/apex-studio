from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from kernel.learning import AgentLearning


def _default_db_path() -> Path:
    apex_home = Path(os.environ.get("APEX_HOME") or Path(__file__).resolve().parents[2])
    return Path(os.environ.get("APEX_DB") or apex_home / "db" / "apex_state.db")


def _learning(db_path: str | Path | None = None) -> AgentLearning:
    return AgentLearning(db_path or _default_db_path())


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path or _default_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _extract_topic_keywords(post_content: str, limit: int = 5) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{3,}", post_content.lower())
    stopwords = {
        "this", "that", "with", "from", "have", "will", "your", "about", "into", "their",
        "there", "what", "when", "where", "which", "while", "were", "been", "being", "over",
        "more", "than", "they", "them", "then", "also", "just", "because", "using", "used",
        "only", "much", "very", "here", "some", "most", "make", "made", "after", "before",
        "content", "post", "thread", "linkedin", "instagram", "tiktok", "twitter", "platform",
    }
    counts = Counter(word for word in words if word not in stopwords)
    return [word for word, _count in counts.most_common(limit)]


def _infer_structure_type(post_content: str) -> str:
    lines = [line.strip() for line in post_content.splitlines() if line.strip()]
    bullet_count = sum(1 for line in lines if line.startswith(("-", "*", "•")) or re.match(r"^\d+[.)]", line))
    if bullet_count >= 3:
        return "listicle"
    if len(lines) >= 4 and all(len(line) < 120 for line in lines[:4]):
        return "thread"
    if "carousel" in post_content.lower():
        return "carousel"
    return "narrative"


def _infer_hook_style(post_content: str) -> str:
    first_line = next((line.strip() for line in post_content.splitlines() if line.strip()), "")
    if not first_line:
        return "unknown"
    if first_line.endswith("?"):
        return "question"
    if re.search(r"\b(unpopular|contrarian|wrong|myth|truth)\b", first_line.lower()):
        return "contrarian"
    if re.search(r"\b(how|why|what)\b", first_line.lower()):
        return "how_to"
    return "statement"


def _infer_post_length(post_content: str) -> str:
    words = len(post_content.split())
    if words < 80:
        return "short"
    if words < 220:
        return "medium"
    return "long"


def _published_datetime(published_at: str | None = None) -> datetime:
    if published_at:
        try:
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _base_metrics(post_content: str, published_at: str) -> dict[str, Any]:
    dt = _published_datetime(published_at)
    return {
        "post_content": post_content,
        "published_at": dt.isoformat(),
        "topic_keywords": _extract_topic_keywords(post_content),
        "structure_type": _infer_structure_type(post_content),
        "hook_style": _infer_hook_style(post_content),
        "post_length": _infer_post_length(post_content),
        "time_posted": dt.strftime("%A %H:%M UTC"),
        "status": "published",
    }


def track_publish(
    workspace_id: str,
    task_id: str,
    platform: str,
    post_content: str,
    published_at: str,
    db_path: str | Path | None = None,
) -> str:
    learning = _learning(db_path)
    metrics = _base_metrics(post_content, published_at)
    return learning.record_performance(workspace_id, task_id, platform, metrics)



def record_engagement(
    workspace_id: str,
    task_id: str,
    metrics: dict[str, Any],
    db_path: str | Path | None = None,
) -> str:
    learning = _learning(db_path)
    conn = _connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT id, platform, metrics_json
            FROM performance_records
            WHERE workspace_id = ? AND task_id = ?
            ORDER BY recorded_at DESC, rowid DESC
            LIMIT 1
            """,
            (workspace_id, task_id),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        platform = str(metrics.get("platform") or "linkedin")
        return learning.record_performance(workspace_id, task_id, platform, metrics)

    merged = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
    merged.update(metrics)
    engagement_record_id = learning.record_performance(workspace_id, task_id, str(row["platform"]), merged)
    return engagement_record_id



def generate_weekly_digest(workspace_id: str, db_path: str | Path | None = None) -> dict[str, Any]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT task_id, platform, metrics_json, recorded_at
            FROM performance_records
            WHERE workspace_id = ?
            ORDER BY recorded_at DESC, rowid DESC
            """,
            (workspace_id,),
        ).fetchall()
    finally:
        conn.close()

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    latest_by_task: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        recorded_at = datetime.fromisoformat(str(row["recorded_at"]).replace(" ", "T") + "+00:00")
        if recorded_at < cutoff:
            continue
        key = (str(row["task_id"]), str(row["platform"]))
        if key in latest_by_task:
            continue
        metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        latest_by_task[key] = {
            "task_id": str(row["task_id"]),
            "platform": str(row["platform"]),
            "metrics": metrics,
            "recorded_at": str(row["recorded_at"]),
        }

    records = list(latest_by_task.values())
    by_platform: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_platform.setdefault(record["platform"], []).append(record)

    published_count = sum(1 for record in records if record["metrics"].get("status") == "published")
    total_engagement = sum(float(record["metrics"].get("engagement_score", 0) or 0) for record in records)
    avg_engagement = round(total_engagement / len(records), 2) if records else 0.0
    top_record = max(records, key=lambda r: float(r["metrics"].get("engagement_score", 0) or 0), default=None)

    platform_summary: dict[str, dict[str, Any]] = {}
    learning = _learning(db_path)
    for platform, platform_records in by_platform.items():
        avg_score = round(
            sum(float(r["metrics"].get("engagement_score", 0) or 0) for r in platform_records) / len(platform_records),
            2,
        ) if platform_records else 0.0
        platform_summary[platform] = {
            "count": len(platform_records),
            "avg_engagement_score": avg_score,
            "top_patterns": learning.get_top_patterns(workspace_id, platform, n=3),
        }

    return {
        "workspace_id": workspace_id,
        "window": "7d",
        "total_posts": published_count,
        "records_considered": len(records),
        "avg_engagement_score": avg_engagement,
        "best_post": top_record,
        "platforms": platform_summary,
    }



def get_recent_published_posts(
    workspace_id: str,
    limit: int = 5,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return the most recent published posts for a workspace.

    Each entry contains task_id, platform, recorded_at, and whatever
    engagement metrics were stored (likes, comments, reposts, impressions,
    post_url, engagement_score, post_content).
    """
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT task_id, platform, metrics_json, recorded_at
            FROM performance_records
            WHERE workspace_id = ?
            ORDER BY recorded_at DESC, rowid DESC
            LIMIT ?
            """,
            (workspace_id, limit),
        ).fetchall()
    finally:
        conn.close()

    posts = []
    for row in rows:
        metrics = json.loads(row["metrics_json"]) if row["metrics_json"] else {}
        posts.append({
            "task_id": str(row["task_id"]),
            "platform": str(row["platform"]),
            "recorded_at": str(row["recorded_at"]),
            "post_url": metrics.get("post_url", ""),
            "post_content": metrics.get("post_content", ""),
            "likes": int(metrics.get("likes", 0) or 0),
            "comments": int(metrics.get("comments", 0) or 0),
            "reposts": int(metrics.get("reposts", 0) or 0),
            "impressions": int(metrics.get("impressions", 0) or 0),
            "engagement_score": metrics.get("engagement_score", None),
        })
    return posts


def format_digest_for_telegram(digest: dict[str, Any]) -> str:
    lines = [
        "📈 Weekly Content Digest",
        f"Workspace: {digest.get('workspace_id', 'unknown')}",
        f"Window: {digest.get('window', '7d')}",
        f"Posts tracked: {digest.get('total_posts', 0)}",
        f"Average engagement score: {digest.get('avg_engagement_score', 0)}",
    ]
    best_post = digest.get("best_post")
    if best_post:
        lines.extend([
            "",
            "🏆 Best Post",
            f"Task: {best_post.get('task_id', 'unknown')} | Platform: {best_post.get('platform', 'unknown')}",
            f"Engagement score: {best_post.get('metrics', {}).get('engagement_score', 0)}",
        ])
    for platform, summary in digest.get("platforms", {}).items():
        lines.extend([
            "",
            f"• {platform}",
            f"  Posts: {summary.get('count', 0)} | Avg score: {summary.get('avg_engagement_score', 0)}",
        ])
        patterns = summary.get("top_patterns", {})
        top_hooks = ", ".join(patterns.get("hook_style", [])[:2]) or "none yet"
        top_topics = ", ".join(patterns.get("topic_keywords", [])[:3]) or "none yet"
        lines.append(f"  Hooks: {top_hooks}")
        lines.append(f"  Topics: {top_topics}")
    return "\n".join(lines)
