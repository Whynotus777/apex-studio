from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any


class EvidenceStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def store_evidence(
        self,
        task_id: str,
        agent_id: str,
        tool_name: str,
        query: str,
        results: list[dict[str, Any]],
    ) -> str:
        evidence_id = f"ev-{uuid.uuid4().hex[:12]}"
        payload = json.dumps(results)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO evidence (id, task_id, agent_id, tool_name, query, results)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (evidence_id, task_id, agent_id, tool_name, query, payload),
            )
            conn.commit()
        return evidence_id

    def get_evidence(self, task_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, task_id, agent_id, tool_name, query, results, created_at
                FROM evidence
                WHERE task_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (task_id,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_evidence_by_id(self, evidence_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, task_id, agent_id, tool_name, query, results, created_at
                FROM evidence
                WHERE id = ?
                """,
                (evidence_id,),
            ).fetchone()
        return self._row_to_dict(row) if row is not None else {}

    def verify_citation(self, task_id: str, url: str) -> bool:
        for evidence in self.get_evidence(task_id):
            for result in evidence.get("results", []):
                if result.get("url") == url:
                    return True
        return False

    def get_capped_evidence(
        self,
        task_id: str,
        max_items: int = 10,
        max_tokens_per_item: int = 150,
    ) -> str:
        """
        Returns a formatted, capped evidence block for prompt injection.

        - Caps at max_items (default 10)
        - Each item: title, URL, and snippet capped at max_tokens_per_item chars
        - If more items exist than max_items, appends a "Showing N of M" notice
        - Total block stays under ~2000 tokens

        Returns empty string if no evidence exists.
        """
        evidence_rows = self.get_evidence(task_id)
        if not evidence_rows:
            return ""

        # Flatten individual result objects across all evidence rows
        all_items: list[dict[str, Any]] = []
        for evidence in evidence_rows:
            all_items.extend(evidence.get("results", []))

        if not all_items:
            return ""

        total = len(all_items)
        capped = all_items[:max_items]

        lines = ["## Search Evidence"]
        for item in capped:
            title = (item.get("title") or "Untitled").strip()
            url = (item.get("url") or "").strip()
            snippet = (item.get("snippet") or "").strip()
            if len(snippet) > max_tokens_per_item:
                # Break at a word boundary to avoid mid-word cuts
                truncated = snippet[:max_tokens_per_item].rsplit(" ", 1)[0]
                snippet = truncated + "…"
            if snippet:
                lines.append(f"- [{title}]({url}) — {snippet}")
            else:
                lines.append(f"- [{title}]({url})")

        if total > max_items:
            lines.append(
                f"\nShowing {max_items} of {total} sources. "
                "Narrow your search for more specific results."
            )

        return "\n".join(lines)

    def format_for_prompt(self, task_id: str) -> str:
        evidence_rows = self.get_evidence(task_id)
        if not evidence_rows:
            return "## Search Evidence\nNone"

        lines = ["## Search Evidence"]
        for evidence in evidence_rows:
            lines.append(
                f"- Tool: {evidence['tool_name']} | Agent: {evidence['agent_id']} | Query: {evidence['query']}"
            )
            for idx, result in enumerate(evidence.get("results", []), start=1):
                title = result.get("title", "")
                url = result.get("url", "")
                snippet = result.get("snippet", "")
                lines.append(f"  {idx}. {title}")
                lines.append(f"     URL: {url}")
                lines.append(f"     Snippet: {snippet}")
        return "\n".join(lines)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["results"] = json.loads(data["results"])
        return data
