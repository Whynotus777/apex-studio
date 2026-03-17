#!/usr/bin/env python3
"""
spawn_context.py — Single-process context builder for spawn-agent.sh.

Combines:
  1. Evidence check / web search (replaces inline search_evidence.py block)
  2. Learning context loading (replaces learning_loader.py subprocess)

Prints combined output (evidence block + learning context) to stdout.
Called once by spawn-agent.sh instead of launching separate subprocesses.

Env vars: APEX_HOME, APEX_DB, APEX_AGENT, APEX_TASK
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

APEX_HOME = os.environ.get("APEX_HOME", str(Path(__file__).resolve().parents[1]))
DB_PATH = os.environ.get("APEX_DB", str(Path(APEX_HOME) / "db" / "apex_state.db"))
AGENT_NAME = os.environ.get("APEX_AGENT", "")
TASK_ID = os.environ.get("APEX_TASK", "")

sys.path.insert(0, APEX_HOME)


def generate_queries(task_title: str, task_text: str) -> list[str]:
    """Use the configured model to generate 3 focused search queries.

    Args:
        task_title: The bare task title — used as fallback query if the model fails.
        task_text:  Full context (title + description) — used as model input only.
    """
    prompt = (
        "Generate exactly 3 short web search queries (one per line, no numbering, "
        "no extra text) for this research task. Prioritize sources from the last 2 weeks. "
        "Accept sources up to 6 months old only if highly relevant. Add '2026' or "
        "'March 2026' to at least one query to bias toward recent results.\n" + task_text
    )
    # Fallback: use the bare task title — always a clean, searchable query.
    # Never fall back to task_text: descriptions include workspace metadata
    # (e.g. "Workspace: ws-abc ## Source Preferences ...") that produces zero results.
    fallback = [task_title.strip()[:150]] if task_title.strip() else [task_text[:150]]
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as sf:
            sf.write("You are a search query generator. Output only queries, one per line.")
            sys_path = sf.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as uf:
            uf.write(prompt)
            usr_path = uf.name
        result = subprocess.run(
            [
                "python3",
                os.path.join(APEX_HOME, "kernel", "call_model.py"),
                "gemini-3-flash-preview",
                sys_path,
                usr_path,
                "0.3",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ},
        )
        os.unlink(sys_path)
        os.unlink(usr_path)
        lines = [
            line.strip().lstrip("•-*0123456789. ")
            for line in result.stdout.strip().split("\n")
            if line.strip()
        ]
        return lines[:3] if lines else fallback
    except Exception as exc:
        print(f"[spawn_context] generate_queries failed: {exc}", file=sys.stderr)
        return fallback


def build_evidence() -> str:
    """Return the formatted evidence block (## Search Evidence ...)."""
    try:
        from kernel.evidence import EvidenceStore
        from kernel.api import ApexKernel

        ev = EvidenceStore(DB_PATH)
        k = ApexKernel()

        # Fast path — reuse existing evidence for this task
        if ev.get_evidence(TASK_ID):
            return ev.format_for_prompt(TASK_ID)

        # Check tool grant
        tools = k.get_agent_tools(AGENT_NAME)
        has_search = any(
            t.get("tool_id") == "web_search" or t.get("name") == "web_search"
            for t in tools
        )

        if not has_search:
            # Workspace inheritance: find most recent evidence from any sibling agent
            ws_prefix = AGENT_NAME.rsplit("-", 1)[0]
            if ws_prefix.startswith("ws-"):
                conn = sqlite3.connect(DB_PATH)
                row = conn.execute(
                    """SELECT task_id FROM evidence
                       WHERE agent_id LIKE ? AND task_id != ?
                       ORDER BY created_at DESC LIMIT 1""",
                    (ws_prefix + "-%", TASK_ID),
                ).fetchone()
                conn.close()
                if row and ev.get_evidence(row[0]):
                    return ev.format_for_prompt(row[0])
            return "## Search Evidence\n(none available)"

        # Agent has search grant — run multi-query search
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT title, description FROM tasks WHERE id=?", (TASK_ID,)
        ).fetchone()
        conn.close()
        if not row or not row[0]:
            return "## Search Evidence\n(none available)"

        title, description = row
        task_text = (title + ". " + (description or "")).strip()
        queries = generate_queries(task_title=title, task_text=task_text)

        from kernel.tool_adapter import execute_tool

        seen_urls: set[str] = set()
        any_results = False
        for query in queries:
            result = execute_tool("web_search", {"query": query, "max_results": 5})
            if result.get("status") != "ok" or not result.get("results"):
                continue
            deduped = [r for r in result["results"] if r.get("url") not in seen_urls]
            if not deduped:
                continue
            seen_urls.update(r["url"] for r in deduped)
            ev.store_evidence(TASK_ID, AGENT_NAME, "web_search", query, deduped)
            any_results = True

        if not any_results:
            print(f"[spawn_context] web_search returned 0 results for all queries: {queries}", file=sys.stderr)
            return "## Search Evidence\n(none available)"
        return ev.format_for_prompt(TASK_ID)
    except Exception as exc:
        print(f"[spawn_context] build_evidence error: {exc}", file=sys.stderr)
        return "## Search Evidence\n(none available)"


def build_learning() -> str:
    """Return learning context string (may be empty)."""
    try:
        from kernel.learning_loader import load_learning_context
        return load_learning_context(AGENT_NAME, TASK_ID, DB_PATH)
    except Exception:
        return ""


def main() -> None:
    parts: list[str] = []

    if TASK_ID:
        evidence = build_evidence()
        if evidence:
            parts.append(evidence)

    learning = build_learning()
    if learning:
        parts.append(learning)

    if parts:
        print("\n\n".join(parts))


if __name__ == "__main__":
    main()
