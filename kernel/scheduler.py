"""APEX Scheduler — background task creation from team schedules.

Activated only when ENABLE_SCHEDULER=true in the environment.
Checks every 60 seconds. Uses per-workspace lease rows in scheduler_runs
to guarantee at-most-once task creation per scheduled interval.

Wave 2 note: pipeline chain triggering is intentionally omitted here.
This module only creates the task record and logs the run.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

log = logging.getLogger("apex.scheduler")

# ---------------------------------------------------------------------------
# Cron helpers (stdlib-only, supports standard 5-field expressions)
# ---------------------------------------------------------------------------

def _field_matches(value: int, field: str, lo: int, hi: int) -> bool:
    """Return True if *value* satisfies cron *field* (min..hi inclusive)."""
    if field == "*":
        return True
    results: list[bool] = []
    for part in field.split(","):
        if "/" in part:
            range_part, step_str = part.split("/", 1)
            step = int(step_str)
            if range_part == "*":
                start, end = lo, hi
            elif "-" in range_part:
                start, end = (int(x) for x in range_part.split("-", 1))
            else:
                start = end = int(range_part)
            results.append(value in range(start, end + 1, step))
        elif "-" in part:
            start, end = (int(x) for x in part.split("-", 1))
            results.append(start <= value <= end)
        else:
            results.append(value == int(part))
    return any(results)


def next_cron_run(expr: str, after: datetime) -> datetime:
    """Return the next UTC datetime at which *expr* fires after *after*.

    *expr* must be a standard 5-field cron expression:
        minute hour day-of-month month day-of-week

    Raises ValueError for malformed expressions.
    Maximum search window: 4 years (avoids infinite loops on bad input).
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Cron expression must have 5 fields, got: {expr!r}")
    f_min, f_hr, f_dom, f_mon, f_dow = parts

    # Start 1 minute after *after* (we never fire at the exact same minute)
    candidate = after.replace(second=0, microsecond=0, tzinfo=timezone.utc) + timedelta(minutes=1)
    deadline = candidate + timedelta(days=366 * 4)

    while candidate < deadline:
        if not _field_matches(candidate.month, f_mon, 1, 12):
            # Skip to first day of next month
            if candidate.month == 12:
                candidate = candidate.replace(year=candidate.year + 1, month=1, day=1,
                                               hour=0, minute=0)
            else:
                candidate = candidate.replace(month=candidate.month + 1, day=1,
                                               hour=0, minute=0)
            continue
        if not _field_matches(candidate.day, f_dom, 1, 31):
            candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if not _field_matches(candidate.weekday(), f_dow, 0, 6):
            # cron weekday: 0=Sunday or 0=Monday depending on flavour.
            # We follow POSIX: 0=Sunday, 1=Monday ... 6=Saturday.
            # Python weekday(): 0=Monday ... 6=Sunday → convert.
            posix_dow = (candidate.weekday() + 1) % 7
            if not _field_matches(posix_dow, f_dow, 0, 6):
                candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
                continue
        if not _field_matches(candidate.hour, f_hr, 0, 23):
            candidate = candidate.replace(minute=0) + timedelta(hours=1)
            continue
        if not _field_matches(candidate.minute, f_min, 0, 59):
            candidate += timedelta(minutes=1)
            continue
        return candidate

    raise ValueError(f"Could not find next run for cron expression {expr!r} within 4 years.")


def interval_floor(expr: str, at: datetime) -> datetime:
    """Return the most recent firing time of *expr* at or before *at*.

    Used for idempotency: two scheduler ticks in the same minute return the
    same slot, preventing duplicate task creation.
    """
    # Search backwards from the minute containing *at*.
    candidate = at.replace(second=0, microsecond=0, tzinfo=timezone.utc)
    deadline = candidate - timedelta(days=366 * 4)
    while candidate > deadline:
        parts = expr.strip().split()
        if len(parts) != 5:
            raise ValueError(f"Cron expression must have 5 fields, got: {expr!r}")
        f_min, f_hr, f_dom, f_mon, f_dow = parts
        posix_dow = (candidate.weekday() + 1) % 7
        if (
            _field_matches(candidate.month, f_mon, 1, 12)
            and _field_matches(candidate.day, f_dom, 1, 31)
            and _field_matches(posix_dow, f_dow, 0, 6)
            and _field_matches(candidate.hour, f_hr, 0, 23)
            and _field_matches(candidate.minute, f_min, 0, 59)
        ):
            return candidate
        candidate -= timedelta(minutes=1)
    raise ValueError(f"Could not find previous run for cron expression {expr!r} within 4 years.")


# ---------------------------------------------------------------------------
# SchedulerService
# ---------------------------------------------------------------------------

class SchedulerService:
    """Background scheduler that creates tasks from enabled team_schedules rows.

    Usage::

        svc = SchedulerService(db_path)
        svc.start()          # no-op unless ENABLE_SCHEDULER=true
        # …application runs…
        svc.stop()
    """

    TICK_SECONDS = 60

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        if os.environ.get("ENABLE_SCHEDULER", "").lower() != "true":
            log.info("Scheduler disabled (ENABLE_SCHEDULER != true). Skipping start.")
            return
        self._ensure_tables()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="apex-scheduler")
        self._thread.start()
        log.info("SchedulerService started (tick=%ds).", self.TICK_SECONDS)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        log.info("SchedulerService stopped.")

    # ── main loop ─────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("Unhandled error in scheduler tick.")
            self._stop_event.wait(timeout=self.TICK_SECONDS)

    def _tick(self) -> None:
        now = datetime.now(timezone.utc)
        schedules = self._load_due_schedules(now)
        for sched in schedules:
            try:
                self._fire(sched, now)
            except Exception:
                log.exception("Failed to fire schedule for workspace %s.", sched["workspace_id"])

    # ── schedule evaluation ───────────────────────────────────────────

    def _load_due_schedules(self, now: datetime) -> list[dict]:
        """Return enabled schedules whose next_run_at <= now."""
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT workspace_id, schedule_type, cron_expression,
                       next_run_at, last_run_at, default_mission
                FROM team_schedules
                WHERE enabled = 1
                  AND default_mission IS NOT NULL
                  AND default_mission != ''
                  AND next_run_at <= ?
                """,
                (now_str,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _fire(self, sched: dict, now: datetime) -> None:
        workspace_id: str = sched["workspace_id"]
        cron_expr: str = sched["cron_expression"]
        mission: str = sched["default_mission"]

        # Compute the slot (interval floor) for idempotency key.
        try:
            slot = interval_floor(cron_expr, now)
        except ValueError:
            log.warning("Could not compute slot for %s / %r; skipping.", workspace_id, cron_expr)
            return
        slot_str = slot.strftime("%Y-%m-%d %H:%M:%S")

        conn = self._connect()
        try:
            # Acquire per-workspace lease — skip if already fired for this slot.
            existing = conn.execute(
                "SELECT id FROM scheduler_runs WHERE workspace_id = ? AND scheduled_for = ?",
                (workspace_id, slot_str),
            ).fetchone()
            if existing:
                log.debug("Slot %s already fired for workspace %s — skipping.", slot_str, workspace_id)
                return

            # Create the task record.
            task_id = f"sched-{uuid.uuid4().hex[:12]}"
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                """
                INSERT INTO tasks (id, title, description, status, workspace_id, created_at)
                VALUES (?, ?, ?, 'backlog', ?, ?)
                """,
                (task_id, f"Scheduled: {mission[:80]}", mission, workspace_id, now_str),
            )

            # Log the run for idempotency.
            run_id = f"sr-{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO scheduler_runs (id, workspace_id, scheduled_for, task_id, fired_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_id, workspace_id, slot_str, task_id, now_str),
            )

            # Advance next_run_at.
            try:
                next_run = next_cron_run(cron_expr, now)
                next_run_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                next_run_str = None  # leave stale; operator must fix expression

            conn.execute(
                """
                UPDATE team_schedules
                SET last_run_at = ?, next_run_at = ?
                WHERE workspace_id = ?
                """,
                (now_str, next_run_str, workspace_id),
            )
            conn.commit()
            log.info("Scheduler fired task %s for workspace %s (slot=%s).", task_id, workspace_id, slot_str)
        finally:
            conn.close()

    # ── DDL ───────────────────────────────────────────────────────────

    def _ensure_tables(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA_SQL)
            conn.commit()
        finally:
            conn.close()

    # ── DB helpers ────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        return conn

    # ── Public helpers (used by API endpoints) ────────────────────────

    def upsert_schedule(
        self,
        workspace_id: str,
        schedule_type: str,
        cron_expression: str,
        default_mission: str,
        enabled: bool = True,
    ) -> dict:
        """Create or replace the schedule for *workspace_id*."""
        if not default_mission or not default_mission.strip():
            raise ValueError("default_mission must be set before enabling a schedule.")
        self._ensure_tables()
        # Validate cron expression by computing next run.
        now = datetime.now(timezone.utc)
        next_run = next_cron_run(cron_expression, now)
        next_run_str = next_run.strftime("%Y-%m-%d %H:%M:%S")
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")

        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO team_schedules
                    (workspace_id, schedule_type, cron_expression, next_run_at,
                     last_run_at, enabled, default_mission, created_at)
                VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
                ON CONFLICT(workspace_id) DO UPDATE SET
                    schedule_type    = excluded.schedule_type,
                    cron_expression  = excluded.cron_expression,
                    next_run_at      = excluded.next_run_at,
                    enabled          = excluded.enabled,
                    default_mission  = excluded.default_mission
                """,
                (workspace_id, schedule_type, cron_expression, next_run_str,
                 1 if enabled else 0, default_mission, now_str),
            )
            conn.commit()
        finally:
            conn.close()

        return self.get_schedule(workspace_id)

    def get_schedule(self, workspace_id: str) -> dict | None:
        """Return the schedule row for *workspace_id*, or None."""
        self._ensure_tables()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM team_schedules WHERE workspace_id = ?",
                (workspace_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def delete_schedule(self, workspace_id: str) -> bool:
        """Delete the schedule for *workspace_id*. Returns True if a row was removed."""
        self._ensure_tables()
        conn = self._connect()
        try:
            cur = conn.execute(
                "DELETE FROM team_schedules WHERE workspace_id = ?",
                (workspace_id,),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Schema (applied lazily on first use)
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS team_schedules (
    workspace_id     TEXT PRIMARY KEY,
    schedule_type    TEXT NOT NULL DEFAULT 'custom',
    cron_expression  TEXT NOT NULL,
    next_run_at      TEXT,
    last_run_at      TEXT,
    enabled          INTEGER NOT NULL DEFAULT 1,
    default_mission  TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scheduler_runs (
    id             TEXT PRIMARY KEY,
    workspace_id   TEXT NOT NULL,
    scheduled_for  TEXT NOT NULL,
    task_id        TEXT,
    fired_at       TEXT NOT NULL,
    UNIQUE(workspace_id, scheduled_for)
);
CREATE INDEX IF NOT EXISTS idx_scheduler_runs_ws
    ON scheduler_runs(workspace_id, scheduled_for);
"""
