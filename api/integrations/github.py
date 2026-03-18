"""
GitHub OAuth integration for APEX.

Endpoints
---------
GET  /api/integrations/github/auth           Start OAuth flow (redirect to GitHub)
GET  /api/integrations/github/callback       Handle OAuth callback, store token
GET  /api/integrations/github/status         Connection status for the user
GET  /api/integrations/github/repos          List accessible repos
POST /api/integrations/github/create-issue   Create a GitHub issue from task output
DELETE /api/integrations/github/disconnect   Remove stored token

Configuration (env vars)
------------------------
GITHUB_CLIENT_ID      — from github.com/settings/applications → OAuth Apps → New
GITHUB_CLIENT_SECRET  — same place
GITHUB_REDIRECT_URI   — default: http://localhost:8000/api/integrations/github/callback
UI_BASE_URL           — default: http://localhost:3000 (post-auth redirect target)

Storage
-------
Tokens are persisted in the `integrations` table in db/apex_state.db.
The table is created automatically on first import via _ensure_schema().
"""

from __future__ import annotations

import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import requests as _requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

try:
    from github import Auth, Github, GithubException
except ImportError:  # pragma: no cover
    Github = None  # type: ignore[assignment]
    Auth = None  # type: ignore[assignment]
    GithubException = Exception  # type: ignore[assignment,misc]


# ── Configuration ─────────────────────────────────────────────────────────────

_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "")
_REDIRECT_URI = os.environ.get(
    "GITHUB_REDIRECT_URI",
    "http://localhost:8000/api/integrations/github/callback",
)
_UI_BASE_URL = os.environ.get("UI_BASE_URL", "http://localhost:3000")

# Scopes needed: repo for issue creation / repo listing, user:email for profile
_SCOPES = "repo,user:email"

# ── Database ──────────────────────────────────────────────────────────────────

_APEX_HOME = Path(__file__).resolve().parents[2]
_DB_PATH = _APEX_HOME / "db" / "apex_state.db"


def _ensure_schema() -> None:
    """Create the `integrations` table if it does not exist yet."""
    with sqlite3.connect(_DB_PATH) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS integrations (
                id                TEXT PRIMARY KEY,
                provider          TEXT NOT NULL,
                user_id           TEXT NOT NULL DEFAULT 'default',
                access_token      TEXT NOT NULL,
                token_type        TEXT DEFAULT 'bearer',
                scope             TEXT,
                github_login      TEXT,
                github_name       TEXT,
                github_avatar_url TEXT,
                created_at        TEXT DEFAULT (datetime('now')),
                updated_at        TEXT DEFAULT (datetime('now')),
                UNIQUE(provider, user_id)
            )
            """
        )
        conn.commit()


# Run once at import time — idempotent, harmless on every restart
_ensure_schema()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _get_token(user_id: str = "default") -> str | None:
    with _db() as conn:
        row = conn.execute(
            "SELECT access_token FROM integrations WHERE provider = 'github' AND user_id = ?",
            (user_id,),
        ).fetchone()
    return str(row["access_token"]) if row else None


def _upsert_token(
    user_id: str,
    access_token: str,
    scope: str,
    github_login: str,
    github_name: str | None,
    github_avatar_url: str | None,
) -> None:
    integration_id = str(uuid.uuid4())
    with _db() as conn:
        conn.execute(
            """
            INSERT INTO integrations
                (id, provider, user_id, access_token, scope,
                 github_login, github_name, github_avatar_url)
            VALUES (?, 'github', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, user_id) DO UPDATE SET
                access_token      = excluded.access_token,
                scope             = excluded.scope,
                github_login      = excluded.github_login,
                github_name       = excluded.github_name,
                github_avatar_url = excluded.github_avatar_url,
                updated_at        = datetime('now')
            """,
            (
                integration_id, user_id, access_token, scope,
                github_login, github_name, github_avatar_url,
            ),
        )


def _require_token(user_id: str = "default") -> str:
    token = _get_token(user_id)
    if not token:
        raise HTTPException(
            status_code=401,
            detail=(
                "GitHub not connected. "
                "Visit /api/integrations/github/auth to start the OAuth flow."
            ),
        )
    return token


# ── CSRF state store (in-memory, single-user local dev) ───────────────────────

_STATE_TTL = 600  # 10 minutes
_oauth_states: dict[str, float] = {}  # state_token → expiry_unix_ts


def _new_state() -> str:
    now = time.time()
    # Prune expired entries to avoid unbounded growth
    expired = [k for k, v in _oauth_states.items() if v < now]
    for k in expired:
        del _oauth_states[k]
    state = secrets.token_urlsafe(32)
    _oauth_states[state] = now + _STATE_TTL
    return state


def _consume_state(state: str) -> bool:
    """Return True and remove state if valid and unexpired."""
    expiry = _oauth_states.pop(state, None)
    return expiry is not None and expiry > time.time()


# ── PyGithub helpers ──────────────────────────────────────────────────────────

def _gh(token: str) -> Any:
    """Return an authenticated PyGithub client."""
    if Github is None:
        raise HTTPException(
            status_code=500,
            detail="PyGithub is not installed. Run: pip install PyGithub",
        )
    return Github(auth=Auth.Token(token))  # type: ignore[union-attr]


def _gh_error(exc: Exception) -> HTTPException:
    """Convert a GithubException into an HTTPException with a clean message."""
    status = getattr(exc, "status", 502)
    data = getattr(exc, "data", {})
    msg = data.get("message", str(exc)) if isinstance(data, dict) else str(exc)
    return HTTPException(status_code=status, detail=f"GitHub API error: {msg}")


# ── Router ────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/integrations/github", tags=["github"])


# ── 1. Auth — start OAuth flow ────────────────────────────────────────────────

@router.get("/auth")
def github_auth() -> RedirectResponse:
    """
    Redirect the browser to GitHub's OAuth authorization page.

    Required env vars: GITHUB_CLIENT_ID
    After authorization, GitHub calls /callback.

    curl -v http://localhost:8000/api/integrations/github/auth
    """
    if not _CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="GITHUB_CLIENT_ID is not set. Add it to .env.",
        )
    state = _new_state()
    url = (
        "https://github.com/login/oauth/authorize"
        f"?client_id={_CLIENT_ID}"
        f"&redirect_uri={_REDIRECT_URI}"
        f"&scope={_SCOPES}"
        f"&state={state}"
    )
    return RedirectResponse(url=url)


# ── 2. Callback — exchange code for token ─────────────────────────────────────

@router.get("/callback")
def github_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(default=None),
    error_description: str | None = Query(default=None),
) -> RedirectResponse:
    """
    GitHub OAuth callback. Exchanges the authorization code for an access token,
    fetches the user profile, and persists both to the integrations table.

    On success: redirects to {UI_BASE_URL}/settings?connected=github&login=<user>
    On failure: redirects to {UI_BASE_URL}/settings?github_error=<reason>
    """
    def _fail(reason: str) -> RedirectResponse:
        return RedirectResponse(
            url=f"{_UI_BASE_URL}/settings?github_error={reason}"
        )

    # User denied the authorization request
    if error:
        return _fail(error)

    # Validate CSRF state
    if not _consume_state(state):
        return _fail("invalid_state")

    if not _CLIENT_SECRET:
        return _fail("GITHUB_CLIENT_SECRET_not_configured")

    # Exchange authorization code for access token
    try:
        resp = _requests.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": _CLIENT_ID,
                "client_secret": _CLIENT_SECRET,
                "code": code,
                "redirect_uri": _REDIRECT_URI,
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
    except _requests.RequestException as exc:
        return _fail(f"token_exchange_failed: {exc}")

    data: dict[str, Any] = resp.json()
    if "error" in data:
        return _fail(str(data["error"]))

    access_token: str = data.get("access_token", "")
    scope: str = data.get("scope", "")

    if not access_token:
        return _fail("no_access_token_returned")

    # Fetch GitHub profile to store alongside the token
    try:
        gh = _gh(access_token)
        user = gh.get_user()
        _upsert_token(
            user_id="default",
            access_token=access_token,
            scope=scope,
            github_login=user.login,
            github_name=user.name or "",
            github_avatar_url=user.avatar_url or "",
        )
    except Exception:
        # Token is valid even if profile fetch fails — store what we have
        _upsert_token(
            user_id="default",
            access_token=access_token,
            scope=scope,
            github_login="unknown",
            github_name=None,
            github_avatar_url=None,
        )
        return RedirectResponse(
            url=f"{_UI_BASE_URL}/settings?connected=github&login=unknown"
        )

    return RedirectResponse(
        url=f"{_UI_BASE_URL}/settings?connected=github&login={user.login}"
    )


# ── 3. Status ─────────────────────────────────────────────────────────────────

@router.get("/status")
def github_status(user_id: str = "default") -> dict[str, Any]:
    """
    Return GitHub connection status.

    Returns {connected: true, login, name, avatar_url, scope, connected_at}
    or     {connected: false}

    curl http://localhost:8000/api/integrations/github/status | python3 -m json.tool
    """
    with _db() as conn:
        row = conn.execute(
            """
            SELECT github_login, github_name, github_avatar_url, scope, updated_at
            FROM integrations
            WHERE provider = 'github' AND user_id = ?
            """,
            (user_id,),
        ).fetchone()

    if not row:
        return {"connected": False}

    return {
        "connected": True,
        "login": row["github_login"],
        "name": row["github_name"],
        "avatar_url": row["github_avatar_url"],
        "scope": row["scope"],
        "connected_at": row["updated_at"],
    }


# ── 4. List repos ─────────────────────────────────────────────────────────────

@router.get("/repos")
def list_repos(
    user_id: str = "default",
    sort: str = "updated",
    visibility: str = "all",
    per_page: int = Query(default=50, le=100),
) -> list[dict[str, Any]]:
    """
    List repos accessible to the connected GitHub account.

    sort:       updated | created | pushed | full_name (GitHub API values)
    visibility: all | public | private
    per_page:   max 100

    curl http://localhost:8000/api/integrations/github/repos | python3 -m json.tool
    """
    token = _require_token(user_id)
    try:
        gh = _gh(token)
        repos = gh.get_user().get_repos(sort=sort, visibility=visibility)
        result: list[dict[str, Any]] = []
        for repo in repos:
            if len(result) >= per_page:
                break
            result.append(
                {
                    "id": repo.id,
                    "name": repo.name,
                    "full_name": repo.full_name,
                    "description": repo.description,
                    "private": repo.private,
                    "html_url": repo.html_url,
                    "clone_url": repo.clone_url,
                    "language": repo.language,
                    "default_branch": repo.default_branch,
                    "open_issues_count": repo.open_issues_count,
                    "updated_at": (
                        repo.updated_at.isoformat() if repo.updated_at else None
                    ),
                }
            )
        return result
    except GithubException as exc:
        raise _gh_error(exc) from exc


# ── 5. Create issue ───────────────────────────────────────────────────────────

class CreateIssueRequest(BaseModel):
    repo: str
    """Full repo name — 'owner/repo', e.g. 'acme/my-project'."""

    title: str
    """Issue title."""

    body: str = ""
    """Issue body (Markdown). If empty and task_id is provided, built from task output."""

    labels: list[str] = []
    """Optional label names that must already exist on the repo."""

    assignees: list[str] = []
    """Optional GitHub usernames to assign (must have access to the repo)."""

    task_id: str | None = None
    """
    APEX task ID. When provided:
    - body is auto-populated from task title/description if not already set
    - a source attribution line is appended to the body
    """

    user_id: str = "default"


@router.post("/create-issue")
def create_issue(payload: CreateIssueRequest) -> dict[str, Any]:
    """
    Create a GitHub issue from an APEX task output.

    The `task_id` field is optional but recommended — when present, task context
    is appended to the issue body as a source attribution footer, and if `body`
    is empty the task description is used as the body.

    Returns: {number, html_url, title, state, repo, created_at}

    curl -X POST http://localhost:8000/api/integrations/github/create-issue \\
         -H 'Content-Type: application/json' \\
         -d '{"repo":"owner/repo","title":"Implement X","body":"Details...","task_id":"task-abc123"}'
    """
    token = _require_token(payload.user_id)
    body = payload.body

    # Enrich body with APEX task context when task_id is provided
    if payload.task_id:
        try:
            with _db() as conn:
                task = conn.execute(
                    "SELECT title, description FROM tasks WHERE id = ?",
                    (payload.task_id,),
                ).fetchone()
            if task:
                attribution = (
                    f"\n\n---\n"
                    f"_Created from APEX task **{task['title']}** "
                    f"([`{payload.task_id}`](http://localhost:3000/teams))_"
                )
                if not body and task["description"]:
                    body = str(task["description"]) + attribution
                else:
                    body = (body or "") + attribution
        except Exception:
            pass  # attribution is best-effort; never block issue creation

    try:
        gh = _gh(token)
        repo = gh.get_repo(payload.repo)

        create_kwargs: dict[str, Any] = {"title": payload.title, "body": body}
        if payload.labels:
            create_kwargs["labels"] = payload.labels
        if payload.assignees:
            create_kwargs["assignees"] = payload.assignees

        issue = repo.create_issue(**create_kwargs)

        return {
            "number": issue.number,
            "html_url": issue.html_url,
            "title": issue.title,
            "state": issue.state,
            "repo": payload.repo,
            "created_at": (
                issue.created_at.isoformat() if issue.created_at else None
            ),
        }
    except GithubException as exc:
        raise _gh_error(exc) from exc


# ── 6. Disconnect ─────────────────────────────────────────────────────────────

@router.delete("/disconnect")
def github_disconnect(user_id: str = "default") -> dict[str, str]:
    """
    Remove stored GitHub credentials for the user.

    Note: this does NOT revoke the OAuth token on GitHub's side.
    To fully revoke, visit: github.com/settings/applications

    curl -X DELETE http://localhost:8000/api/integrations/github/disconnect
    """
    with _db() as conn:
        conn.execute(
            "DELETE FROM integrations WHERE provider = 'github' AND user_id = ?",
            (user_id,),
        )
    return {"status": "disconnected"}
