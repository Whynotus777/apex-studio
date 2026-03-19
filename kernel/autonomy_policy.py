"""Shared autonomy policy module.

Translates a workspace's autonomy setting (hands_on / managed / autopilot)
and the Critic's review result (verdict + overall_score) into a routing
decision: needs_review | approved | approved_and_publish | blocked.

Usage::

    from kernel.autonomy_policy import decide, save_workspace_autonomy

    outcome = decide("managed", "PASS", 4.2)
    # → "approved"

    save_workspace_autonomy(db_path, workspace_id, "managed")
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Literal

# ── Types ─────────────────────────────────────────────────────────────

AutoonomySetting = Literal["hands_on", "managed", "autopilot"]
PolicyDecision = Literal["needs_review", "approved", "approved_and_publish", "blocked"]

VALID_AUTONOMY: frozenset[str] = frozenset({"hands_on", "managed", "autopilot"})
VALID_VERDICTS: frozenset[str] = frozenset({"PASS", "REVISE", "BLOCK"})

# Score threshold: Critic overall_score must reach this to auto-approve.
PASS_THRESHOLD: float = 4.0


# ── Core decision logic ────────────────────────────────────────────────

def decide(
    autonomy_setting: str,
    verdict: str,
    overall_score: float,
) -> PolicyDecision:
    """Return the routing decision for a reviewed agent output.

    Args:
        autonomy_setting: Workspace autonomy mode — one of
            ``hands_on``, ``managed``, ``autopilot``.
            Unknown values are treated as ``hands_on`` (safest default).
        verdict: Critic verdict — ``PASS``, ``REVISE``, or ``BLOCK``.
            Case-insensitive.
        overall_score: Numeric quality score from the Critic (0–5 scale).

    Returns:
        One of:
        - ``"needs_review"``       — route to human approval queue
        - ``"approved"``           — auto-approve, notify user
        - ``"approved_and_publish"`` — auto-approve and publish without prompt
        - ``"blocked"``            — hard stop, do not publish or approve
    """
    mode = autonomy_setting.lower().strip()
    v = verdict.upper().strip()

    if mode == "hands_on":
        # Always queue for human review regardless of score or verdict.
        return "needs_review"

    if mode == "managed":
        # Auto-approve only on clean PASS above threshold; everything else to review.
        if v == "PASS" and overall_score >= PASS_THRESHOLD:
            return "approved"
        return "needs_review"

    if mode == "autopilot":
        # Hard block on BLOCK verdict.
        if v == "BLOCK":
            return "blocked"
        # Auto-publish on clean PASS above threshold.
        if v == "PASS" and overall_score >= PASS_THRESHOLD:
            return "approved_and_publish"
        # REVISE or low score → still needs human eyes.
        return "needs_review"

    # Unknown setting → safest default.
    return "needs_review"


# ── Schema migration ───────────────────────────────────────────────────

def _ensure_autonomy_column(conn: sqlite3.Connection) -> None:
    """Add autonomy_policy column to workspaces table if absent (idempotent)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(workspaces)")}
    if "autonomy_policy" not in cols:
        conn.execute(
            "ALTER TABLE workspaces ADD COLUMN autonomy_policy TEXT DEFAULT 'hands_on'"
        )
        conn.commit()


# ── Persistence helpers ────────────────────────────────────────────────

def save_workspace_autonomy(
    db_path: str | Path,
    workspace_id: str,
    autonomy_policy: str,
) -> None:
    """Persist the autonomy policy for a workspace.

    Ensures the column exists (safe to call on older databases), then
    writes the value.  Unknown policy strings are stored as-is; callers
    should validate before persisting if strict enforcement is needed.

    Args:
        db_path: Path to the SQLite database file.
        workspace_id: Target workspace identifier.
        autonomy_policy: One of ``hands_on``, ``managed``, ``autopilot``.
    """
    policy = autonomy_policy.lower().strip() if autonomy_policy else "hands_on"
    path = Path(db_path)
    conn = sqlite3.connect(path)
    try:
        _ensure_autonomy_column(conn)
        conn.execute(
            "UPDATE workspaces SET autonomy_policy = ? WHERE id = ?",
            (policy, workspace_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_workspace_autonomy(
    db_path: str | Path,
    workspace_id: str,
) -> str:
    """Return the stored autonomy policy for a workspace (default: ``hands_on``)."""
    path = Path(db_path)
    conn = sqlite3.connect(path)
    try:
        _ensure_autonomy_column(conn)
        row = conn.execute(
            "SELECT autonomy_policy FROM workspaces WHERE id = ?",
            (workspace_id,),
        ).fetchone()
    finally:
        conn.close()

    if row and row[0]:
        return str(row[0])
    return "hands_on"
