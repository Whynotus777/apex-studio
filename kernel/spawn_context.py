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


def _strip_site_operators(query: str) -> str:
    """Remove site: tokens (and any flanking OR/AND) from a search query.

    DuckDuckGo's HTML endpoint ignores or mishandles site: operators,
    causing zero results. Strip them and collapse extra whitespace.
    Example: 'AI agents 2026 site:arxiv.org OR site:github.com'
             → 'AI agents 2026'
    """
    import re
    # Remove each site: token together with any immediately preceding or
    # following OR/AND conjunction so multi-site chains collapse cleanly.
    cleaned = re.sub(
        r'(\s*\b(?:OR|AND)\b\s*)?site:\S+(\s*\b(?:OR|AND)\b\s*)?',
        ' ',
        query,
        flags=re.IGNORECASE,
    )
    # Remove any orphaned leading/trailing OR/AND left over.
    cleaned = re.sub(r'^\s*\b(?:OR|AND)\b\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*\b(?:OR|AND)\b\s*$', '', cleaned, flags=re.IGNORECASE)
    return ' '.join(cleaned.split())


def _strip_metadata_sections(text: str) -> str:
    """Remove workspace metadata blocks from task text before query generation.

    Task descriptions include operational sections (Source Preferences, Platform
    Instructions, Workspace IDs) that are implementation details, not topic signals.
    Feeding these to the query generator causes models to infer topics from domain
    names (e.g. mckinsey.com → "consulting/PE") instead of from the task title.

    Strips any section whose heading matches known metadata patterns, keeping only
    the content that describes what to research.
    """
    import re
    # Remove markdown sections whose headings are metadata, not research topics.
    # Pattern: ## Heading followed by content up to the next ## heading or end of string.
    metadata_headings = re.compile(
        r"##\s*(Source Preferences?|Platform Instructions?|Workspace|"
        r"Preferred Domains?|Voice Reference|Humanize Pass)[^\n]*\n.*?(?=\n##|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    cleaned = metadata_headings.sub("", text)
    # Also strip bare "Workspace: ws-..." lines
    cleaned = re.sub(r"(?m)^Workspace:\s*\S+\s*\n?", "", cleaned)
    cleaned = re.sub(r"(?m)^Issued via \S+\.\s*\n?", "", cleaned)
    return cleaned.strip()


def _load_topics(workspace_id: str) -> str:
    """Return the comma-separated topic string stored for this workspace, or ''."""
    if not workspace_id:
        return ""
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute(
            "SELECT value FROM user_preferences "
            "WHERE workspace_id = ? AND preference_type = 'topic_preference' AND key = 'topics'",
            (workspace_id,),
        ).fetchone()
        conn.close()
        return row[0].strip() if row and row[0] else ""
    except Exception:
        return ""


def generate_queries(task_title: str, task_text: str) -> list[str]:
    """Use the configured model to generate 3 focused search queries.

    Args:
        task_title: The bare task title — used as fallback query if the model fails.
        task_text:  Full context (title + description) — used as model input only.
    """
    # Derive workspace_id from agent name: ws-abc123-scout → ws-abc123
    ws_id = AGENT_NAME.rsplit("-", 1)[0] if "-" in AGENT_NAME else ""
    topics = _load_topics(ws_id)
    topic_guidance = (
        f"Focus research on these topics: {topics}\n" if topics else ""
    )

    # Strip workspace metadata (source preferences, platform instructions, etc.)
    # before sending to the model — domain names must not influence query topics.
    clean_task_text = _strip_metadata_sections(task_text)

    prompt = (
        "Generate exactly 3 short web search queries (one per line, no numbering, "
        "no extra text) for this research task. "
        "IMPORTANT: Do NOT use site: operators — they break the search engine. "
        "Use plain keyword queries only. "
        "Prioritize sources from the last 2 weeks. "
        "Accept sources up to 6 months old only if highly relevant. Add '2026' or "
        "'March 2026' to at least one query to bias toward recent results.\n"
        f"{topic_guidance}{clean_task_text}"
    )
    # Fallback: use the bare task title — always a clean, searchable query.
    # Never fall back to task_text: descriptions include workspace metadata
    # (e.g. "Workspace: ws-abc ## Source Preferences ...") that produces zero results.
    fallback = [task_title.strip()[:150]] if task_title.strip() else [task_text[:150]]
    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as sf:
            sf.write("You are a search query generator. Output only queries, one per line. Never use site: operators.")
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
        # Strip any site: operators the model may have generated despite instructions.
        # This is defense-in-depth: DuckDuckGo returns 0 results for site: queries.
        cleaned = [_strip_site_operators(q) for q in lines if q.strip()]
        # Drop queries that became empty after stripping
        cleaned = [q for q in cleaned if q]
        return cleaned[:3] if cleaned else fallback
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
            return ev.get_capped_evidence(TASK_ID)

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
                    return ev.get_capped_evidence(row[0])
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
        return ev.get_capped_evidence(TASK_ID)
    except Exception as exc:
        print(f"[spawn_context] build_evidence error: {exc}", file=sys.stderr)
        return "## Search Evidence\n(none available)"


def build_topic_constraint() -> str:
    """Return a MANDATORY TOPICS block for Writer agents.

    Injected at the top of the context so it appears before evidence and
    learning context — making it the first thing the model reads.  Only
    emitted for agents whose name ends in '-writer'.  Empty string when
    the agent is not a writer or no topics have been configured.
    """
    if not AGENT_NAME.endswith("-writer"):
        return ""
    ws_id = AGENT_NAME.rsplit("-", 1)[0]
    topics = _load_topics(ws_id)
    if not topics:
        return ""
    return (
        f"## MANDATORY TOPICS — Only write about: {topics}. "
        "Any content outside these topics will be rejected."
    )


def build_learning() -> str:
    """Return learning context string (may be empty)."""
    try:
        from kernel.learning_loader import load_learning_context
        return load_learning_context(AGENT_NAME, TASK_ID, DB_PATH)
    except Exception:
        return ""


def build_mission_brief() -> str:
    """Return mission brief summary for this workspace, or empty string if not set."""
    try:
        ws_id = AGENT_NAME.rsplit("-", 1)[0] if "-" in AGENT_NAME else ""
        if not ws_id.startswith("ws-"):
            return ""
        from kernel.mission_brief import MissionBrief
        mb = MissionBrief(APEX_HOME)
        summary = mb.get_brief_summary(ws_id)
        return summary or ""
    except Exception as exc:
        print(f"[spawn_context] build_mission_brief error: {exc}", file=sys.stderr)
        return ""


def build_document_context() -> str:
    """Return uploaded document context block for this workspace, or empty string.

    Documents are capped at 5000 chars total to prevent context flooding.
    Agents without uploaded documents are unaffected — this is purely additive.
    """
    ws_id = AGENT_NAME.rsplit("-", 1)[0] if "-" in AGENT_NAME else ""
    if not ws_id.startswith("ws-"):
        return ""
    try:
        from kernel.documents import DocumentStore
        ds = DocumentStore(DB_PATH)
        ctx = ds.get_document_context(ws_id, max_chars=5000)
        return ctx or ""
    except Exception as exc:
        print(f"[spawn_context] build_document_context error: {exc}", file=sys.stderr)
        return ""


def main() -> None:
    parts: list[str] = []

    # Topic constraint goes first — writer must see it before any evidence.
    topic_constraint = build_topic_constraint()
    if topic_constraint:
        parts.append(topic_constraint)

    if TASK_ID:
        evidence = build_evidence()
        if evidence:
            parts.append(evidence)

    # Document context injected after evidence, before learning.
    # Agents read uploaded files as additional grounding material.
    doc_context = build_document_context()
    if doc_context:
        parts.append(doc_context)

    learning = build_learning()
    if learning:
        parts.append(learning)

    # Mission brief prepended — first thing agents see.
    brief = build_mission_brief()
    if brief:
        parts.insert(0, brief)

    if parts:
        print("\n\n".join(parts))


if __name__ == "__main__":
    main()
