"""
documents.py — Document storage and extraction for APEX.

Stores files uploaded by users and makes their text content available
to agents as context during spawning.

Supported formats: .pdf, .txt, .md
PDF extraction: pdfplumber (primary) → PyPDF2 (fallback) → raw UTF-8 decode (last resort)

Documents are capped at 5000 chars when injected into agent prompts to
prevent sloppy or verbose PDFs from flooding context windows.
"""
from __future__ import annotations

import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any

_SUPPORTED_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
}

_MAX_INJECT_CHARS = 5000   # hard cap for prompt injection
_EXCERPT_CHARS    = 2000   # chars shown per document in the formatted block
_SUMMARY_CHARS    = 300    # chars used to build the auto-summary


class DocumentStore:
    """
    Stores and retrieves documents uploaded by users.
    Extracts text from PDFs and plain-text formats.
    Documents are context that agents read during their work.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._migrate()

    # ── Public API ──────────────────────────────────────────────────────

    def upload_document(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        workspace_id: str | None = None,
        chat_session_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Store a document and extract its text content.

        Supports: .pdf, .txt, .md (no .docx this sprint)

        Returns: {id, filename, content_type, char_count, summary}
        """
        # Normalise content type based on filename extension when the caller
        # sends a generic type (e.g. "application/octet-stream").
        resolved_type = _resolve_content_type(filename, content_type)
        if resolved_type not in _SUPPORTED_TYPES:
            raise ValueError(
                f"Unsupported file type '{resolved_type}' for '{filename}'. "
                "Supported: PDF, TXT, MD."
            )

        extracted = self._extract_text(file_bytes, resolved_type)
        summary   = _build_summary(extracted, filename)
        doc_id    = f"doc-{uuid.uuid4().hex[:12]}"

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO workspace_documents
                    (id, filename, content_type, workspace_id, chat_session_id,
                     extracted_text, char_count, summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc_id,
                    filename,
                    resolved_type,
                    workspace_id,
                    chat_session_id,
                    extracted,
                    len(extracted),
                    summary,
                ),
            )
            conn.commit()

        return {
            "id":           doc_id,
            "filename":     filename,
            "content_type": resolved_type,
            "char_count":   len(extracted),
            "summary":      summary,
        }

    def get_documents(self, workspace_id: str) -> list[dict[str, Any]]:
        """Return all documents for a workspace, ordered newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, filename, content_type, char_count, summary, created_at
                FROM workspace_documents
                WHERE workspace_id = ?
                ORDER BY created_at DESC
                """,
                (workspace_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_document_context(
        self,
        workspace_id: str,
        max_chars: int = _MAX_INJECT_CHARS,
    ) -> str | None:
        """
        Return a formatted text block for injection into agent prompts.

        Uses SUMMARY + CAPPED EXCERPT per document, not full raw text.
        Prevents sloppy PDFs from polluting context.

        Format:
            ## Uploaded Documents

            ### requirements.pdf
            Summary: A product requirements document describing...

            Key content (excerpt):
            [first ~2000 chars of extracted text, truncated at sentence boundary]
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT filename, extracted_text, summary
                FROM workspace_documents
                WHERE workspace_id = ?
                ORDER BY created_at ASC
                """,
                (workspace_id,),
            ).fetchall()

        if not rows:
            return None

        sections: list[str] = ["## Uploaded Documents"]
        budget = max_chars - len(sections[0]) - 4  # track remaining chars

        for row in rows:
            if budget <= 0:
                break
            filename    = row["filename"]
            summary     = row["summary"] or ""
            full_text   = row["extracted_text"] or ""
            excerpt     = _truncate_at_sentence(full_text, _EXCERPT_CHARS)

            block_lines = [
                f"\n### {filename}",
                f"Summary: {summary}",
                "",
                "Key content (excerpt):",
                excerpt,
            ]
            block = "\n".join(block_lines)

            if len(block) > budget:
                # Trim excerpt so it fits within remaining budget
                overhead = len(block) - len(excerpt)
                allowed  = max(0, budget - overhead)
                excerpt  = _truncate_at_sentence(full_text, allowed)
                block_lines[-1] = excerpt
                block = "\n".join(block_lines)

            sections.append(block)
            budget -= len(block)

        if len(sections) == 1:
            return None  # only header — no documents fit

        return "\n".join(sections)

    def get_document_summary_for_chat(
        self,
        chat_session_id: str,
    ) -> str | None:
        """
        Return a concise per-document summary for the architect.

        Format:
            [Document: requirements.pdf - PRD for customer analytics dashboard, 4200 chars]
            [Document: notes.md - Design notes about API structure, 890 chars]
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT filename, char_count, summary
                FROM workspace_documents
                WHERE chat_session_id = ?
                ORDER BY created_at ASC
                """,
                (chat_session_id,),
            ).fetchall()

        if not rows:
            return None

        lines = [
            f"[Document: {r['filename']} - {r['summary'] or 'no summary'}, {r['char_count']} chars]"
            for r in rows
        ]
        return "\n".join(lines)

    def link_to_workspace(self, chat_session_id: str, workspace_id: str) -> None:
        """
        After a team launches from chat, re-link all documents
        from the chat session to the workspace.
        """
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE workspace_documents
                SET workspace_id = ?
                WHERE chat_session_id = ?
                """,
                (workspace_id, chat_session_id),
            )
            conn.commit()

    # ── Private helpers ─────────────────────────────────────────────────

    def _extract_text(self, file_bytes: bytes, content_type: str) -> str:
        """Extract text from supported file formats."""
        if content_type == "application/pdf":
            return self._extract_pdf(file_bytes)
        # Plain text and markdown
        try:
            return file_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            raise ValueError(f"Failed to decode text file: {exc}") from exc

    @staticmethod
    def _extract_pdf(file_bytes: bytes) -> str:
        """Extract text from PDF bytes using pdfplumber → PyPDF2 → raw fallback."""
        import io

        # ── Attempt 1: pdfplumber ───────────────────────────────────────
        try:
            import pdfplumber  # type: ignore[import]
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                pages = [
                    page.extract_text() or ""
                    for page in pdf.pages
                ]
            text = "\n\n".join(p.strip() for p in pages if p.strip())
            if text.strip():
                return text
        except ImportError:
            pass  # pdfplumber not installed — try next
        except Exception:
            pass  # corrupt PDF or parse error — try next

        # ── Attempt 2: PyPDF2 ───────────────────────────────────────────
        try:
            import PyPDF2  # type: ignore[import]
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            pages = []
            for page in reader.pages:
                try:
                    pages.append(page.extract_text() or "")
                except Exception:
                    pass
            text = "\n\n".join(p.strip() for p in pages if p.strip())
            if text.strip():
                return text
        except ImportError:
            pass
        except Exception:
            pass

        # ── Attempt 3: raw UTF-8 decode (last resort) ───────────────────
        try:
            return file_bytes.decode("utf-8", errors="replace")
        except Exception as exc:
            raise ValueError("Could not extract text from PDF.") from exc

    def _migrate(self) -> None:
        """Create workspace_documents table if it doesn't exist."""
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS workspace_documents (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    workspace_id TEXT,
                    chat_session_id TEXT,
                    extracted_text TEXT NOT NULL,
                    char_count INTEGER NOT NULL,
                    summary TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_workspace_documents_workspace "
                "ON workspace_documents(workspace_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_workspace_documents_chat "
                "ON workspace_documents(chat_session_id)"
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn


# ── Module-level helpers ────────────────────────────────────────────────

def _resolve_content_type(filename: str, declared: str) -> str:
    """
    Resolve the effective content type.
    If the declared type is generic ('application/octet-stream'), infer from extension.
    """
    if declared and declared not in ("application/octet-stream", ""):
        # Normalise common markdown variants
        if declared in ("text/x-markdown", "text/md"):
            return "text/markdown"
        return declared

    ext = Path(filename).suffix.lower()
    mapping = {
        ".pdf": "application/pdf",
        ".txt": "text/plain",
        ".md":  "text/markdown",
    }
    return mapping.get(ext, declared or "application/octet-stream")


def _build_summary(text: str, filename: str) -> str:
    """
    Build a short human-readable summary from extracted text.

    Takes the first meaningful paragraph (up to _SUMMARY_CHARS),
    trimmed to a word boundary.
    """
    # Strip leading whitespace/newlines
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return f"Uploaded file: {filename}"

    # Take first chunk up to _SUMMARY_CHARS chars, break at word boundary
    chunk = cleaned[:_SUMMARY_CHARS]
    if len(cleaned) > _SUMMARY_CHARS:
        # Trim to last word boundary
        last_space = chunk.rfind(" ")
        if last_space > 0:
            chunk = chunk[:last_space]
        chunk = chunk.rstrip(".,;: ") + "..."

    return chunk


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    """
    Truncate text at a sentence boundary no longer than max_chars.
    Falls back to word boundary, then hard cut.
    """
    if len(text) <= max_chars:
        return text

    chunk = text[:max_chars]

    # Try to end at a sentence boundary (. ! ?)
    match = re.search(r"[.!?]\s", chunk[::-1])
    if match:
        cut = max_chars - match.start()
        return text[:cut].rstrip()

    # Fall back to word boundary
    last_space = chunk.rfind(" ")
    if last_space > max_chars // 2:
        return chunk[:last_space].rstrip() + "…"

    return chunk.rstrip() + "…"
