from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)

APEX_HOME = Path(os.environ.get("APEX_HOME") or Path(__file__).resolve().parents[1])
DEFAULT_DB_PATH = Path(os.environ.get("APEX_DB") or APEX_HOME / "db" / "apex_state.db")


class NotificationService:
    def __init__(self, db_path: str | Path | None = None, apex_home: str | Path | None = None) -> None:
        self.apex_home = Path(apex_home or APEX_HOME).resolve()
        self.db_path = Path(db_path or DEFAULT_DB_PATH).resolve()
        self._ensure_notifications_table()

    def notify_review_ready(self, workspace_id: str, task_id: str, task_title: str) -> None:
        self._notify(
            notification_type="review_ready",
            workspace_id=workspace_id,
            task_id=task_id,
            message=f"Review ready: {task_title} ({task_id}) is ready for approval.",
        )

    def notify_auto_published(self, workspace_id: str, task_id: str, task_title: str) -> None:
        self._notify(
            notification_type="auto_published",
            workspace_id=workspace_id,
            task_id=task_id,
            message=f"Auto-published: {task_title} ({task_id}) was published successfully.",
        )

    def notify_error(self, workspace_id: str, task_id: str, message: str) -> None:
        self._notify(
            notification_type="error",
            workspace_id=workspace_id,
            task_id=task_id,
            message=f"Task error: {task_id} failed. {message}",
        )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    def _ensure_notifications_table(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS notifications_sent (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    notification_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    sent_at TEXT DEFAULT (datetime('now')),
                    status TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_notifications_sent_dedupe
                ON notifications_sent(workspace_id, task_id, channel, notification_type)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_notifications_sent_task
                ON notifications_sent(task_id, channel, sent_at DESC)
                """
            )

    def _notify(self, notification_type: str, workspace_id: str, task_id: str, message: str) -> None:
        if workspace_id.startswith("ws-demo"):
            logger.info(
                "Skipping %s notification for demo workspace %s task %s",
                notification_type,
                workspace_id,
                task_id,
            )
            return

        channels = self._connected_channels(workspace_id)
        if not channels:
            logger.info(
                "No notification channels connected for workspace %s task %s: %s",
                workspace_id,
                task_id,
                message,
            )
            self._record_attempt(
                workspace_id=workspace_id,
                task_id=task_id,
                channel="none",
                notification_type=notification_type,
                message=message,
                status="no_channels",
            )
            return

        for channel, integration in channels.items():
            if self._is_duplicate(workspace_id, task_id, channel, notification_type):
                logger.info(
                    "Suppressing duplicate %s notification for task %s via %s",
                    notification_type,
                    task_id,
                    channel,
                )
                continue

            try:
                status = self._send(channel, integration, message)
            except Exception:
                logger.exception(
                    "Notification send failed for workspace %s task %s via %s",
                    workspace_id,
                    task_id,
                    channel,
                )
                status = "failed"

            self._record_attempt(
                workspace_id=workspace_id,
                task_id=task_id,
                channel=channel,
                notification_type=notification_type,
                message=message,
                status=status,
            )

    def _connected_channels(self, workspace_id: str) -> dict[str, sqlite3.Row]:
        providers = {"slack", "telegram"}
        rows: list[sqlite3.Row] = []
        with self._connect() as conn:
            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'integrations'"
            ).fetchone()
            if not table_exists:
                return {}

            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(integrations)").fetchall()
            }
            select_fields = ["provider"]
            for field in ("id", "workspace_id", "access_token", "user_id"):
                if field in columns:
                    select_fields.append(field)

            where_clauses = ["provider IN ('slack', 'telegram')"]
            params: list[Any] = []
            if "workspace_id" in columns:
                where_clauses.append("(workspace_id = ? OR workspace_id IS NULL OR workspace_id = '')")
                params.append(workspace_id)

            if "access_token" in columns:
                where_clauses.append("access_token IS NOT NULL AND TRIM(access_token) <> ''")

            query = f"""
                SELECT {", ".join(select_fields)}
                FROM integrations
                WHERE {' AND '.join(where_clauses)}
                ORDER BY
                    CASE
                        WHEN COALESCE(workspace_id, '') = ? THEN 0
                        WHEN COALESCE(workspace_id, '') = '' THEN 1
                        ELSE 2
                    END,
                    updated_at DESC,
                    created_at DESC
            """
            if "workspace_id" in columns:
                params.append(workspace_id)
            else:
                query = f"""
                    SELECT {", ".join(select_fields)}
                    FROM integrations
                    WHERE {' AND '.join(where_clauses)}
                """

            rows = conn.execute(query, params).fetchall()

        connected: dict[str, sqlite3.Row] = {}
        for row in rows:
            provider = str(row["provider"]).strip().lower()
            if provider not in providers or provider in connected:
                continue
            if provider == "telegram" and not self._telegram_is_configured():
                logger.info("Telegram integration is present but adapter is not configured.")
                continue
            connected[provider] = row
        return connected

    def _telegram_is_configured(self) -> bool:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
        return bool(token and chat_id)

    def _is_duplicate(self, workspace_id: str, task_id: str, channel: str, notification_type: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM notifications_sent
                WHERE workspace_id = ?
                  AND task_id = ?
                  AND channel = ?
                  AND notification_type = ?
                LIMIT 1
                """,
                (workspace_id, task_id, channel, notification_type),
            ).fetchone()
        return row is not None

    def _record_attempt(
        self,
        workspace_id: str,
        task_id: str,
        channel: str,
        notification_type: str,
        message: str,
        status: str,
    ) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO notifications_sent (
                        id,
                        workspace_id,
                        task_id,
                        channel,
                        notification_type,
                        message,
                        sent_at,
                        status
                    )
                    VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        workspace_id,
                        task_id,
                        channel,
                        notification_type,
                        message,
                        status,
                    ),
                )
        except sqlite3.IntegrityError:
            logger.info(
                "Notification attempt already recorded for workspace %s task %s via %s (%s)",
                workspace_id,
                task_id,
                channel,
                notification_type,
            )
        except Exception:
            logger.exception(
                "Failed to record notification attempt for workspace %s task %s",
                workspace_id,
                task_id,
            )

    def _send(self, channel: str, integration: sqlite3.Row, message: str) -> str:
        if channel == "slack":
            return self._send_slack(integration, message)
        if channel == "telegram":
            return self._send_telegram(message)
        raise ValueError(f"Unsupported notification channel: {channel}")

    def _send_slack(self, integration: sqlite3.Row, message: str) -> str:
        slack_channel = (
            os.environ.get("SLACK_NOTIFICATION_CHANNEL")
            or os.environ.get("SLACK_DEFAULT_CHANNEL")
            or os.environ.get("SLACK_CHANNEL")
        )
        if not slack_channel:
            logger.info("Slack integration is connected but no Slack channel is configured.")
            return "skipped_missing_channel"

        try:
            from api.integrations.slack import SlackSendRequest, slack_send
        except Exception:
            logger.exception("Slack notification adapter is unavailable.")
            return "failed"

        try:
            slack_send(
                SlackSendRequest(
                    channel=slack_channel,
                    text=message,
                    integration_id=integration["id"] if "id" in integration.keys() else None,
                )
            )
        except Exception:
            logger.exception("Slack notification delivery failed.")
            return "failed"
        return "sent"

    def _send_telegram(self, message: str) -> str:
        if not self._telegram_is_configured():
            logger.info("Telegram adapter is not configured.")
            return "skipped_not_configured"

        cmd = ["python3", str(self.apex_home / "adapters" / "telegram" / "send_telegram.py"), message]
        try:
            subprocess.Popen(
                cmd,
                cwd=self.apex_home,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=os.environ.copy(),
            )
        except Exception:
            logger.exception("Telegram notification delivery failed.")
            return "failed"
        return "sent"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone notification service runner.")
    parser.add_argument("notification_type", choices=["review_ready", "auto_published", "error"])
    parser.add_argument("workspace_id")
    parser.add_argument("task_id")
    parser.add_argument("payload", help="Task title for review_ready/auto_published, error message for error.")
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    args = _build_parser().parse_args()
    service = NotificationService()
    if args.notification_type == "review_ready":
        service.notify_review_ready(args.workspace_id, args.task_id, args.payload)
    elif args.notification_type == "auto_published":
        service.notify_auto_published(args.workspace_id, args.task_id, args.payload)
    else:
        service.notify_error(args.workspace_id, args.task_id, args.payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
