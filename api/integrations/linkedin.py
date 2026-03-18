"""LinkedIn OAuth2 integration.

Environment variables:
    LINKEDIN_CLIENT_ID       — from LinkedIn Developer Portal
    LINKEDIN_CLIENT_SECRET   — from LinkedIn Developer Portal
    LINKEDIN_REDIRECT_URI    — must match exactly what is registered on LinkedIn
                               default: http://localhost:8000/api/integrations/linkedin/callback
    LINKEDIN_FRONTEND_URL    — where to redirect after OAuth completes
                               default: http://localhost:3000

Endpoints:
    GET  /api/integrations/linkedin/status    → {"connected": bool, "user_name": str|null}
    GET  /api/integrations/linkedin/auth      → redirect to LinkedIn authorization page
    GET  /api/integrations/linkedin/callback  → exchange code, store token, redirect to frontend
    POST /api/integrations/linkedin/post      → publish a text post using stored access token
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

# ── Config ────────────────────────────────────────────────────────────

_CLIENT_ID     = os.environ.get("LINKEDIN_CLIENT_ID", "")
_CLIENT_SECRET = os.environ.get("LINKEDIN_CLIENT_SECRET", "")
_REDIRECT_URI  = os.environ.get(
    "LINKEDIN_REDIRECT_URI",
    "http://localhost:8000/api/integrations/linkedin/callback",
)
_FRONTEND_URL  = os.environ.get("LINKEDIN_FRONTEND_URL", "http://localhost:3000")

_AUTH_URL  = "https://www.linkedin.com/oauth/v2/authorization"
_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_ME_URL    = "https://api.linkedin.com/v2/userinfo"
_POST_URL  = "https://api.linkedin.com/v2/ugcPosts"

# w_member_social = post on behalf of member
# openid profile email = userinfo endpoint (person ID for author URN)
_SCOPES = "openid profile email w_member_social"

_DB_PATH = Path(os.environ.get("APEX_HOME", Path(__file__).parent.parent.parent)) / "db" / "apex_state.db"

# ── In-memory CSRF state store (single-process safe for dev) ─────────

_pending_states: set[str] = set()

# ── DB helpers ────────────────────────────────────────────────────────


def _ensure_table() -> None:
    conn = sqlite3.connect(_DB_PATH)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS linkedin_tokens (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                access_token  TEXT NOT NULL,
                refresh_token TEXT,
                expires_at    TEXT,
                person_id     TEXT,
                person_name   TEXT,
                created_at    TEXT DEFAULT (datetime('now')),
                updated_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.commit()
    finally:
        conn.close()


def _upsert_token(
    access_token: str,
    refresh_token: str | None,
    expires_at: str | None,
    person_id: str | None,
    person_name: str | None,
) -> None:
    conn = sqlite3.connect(_DB_PATH)
    try:
        # Keep only one row — delete old, insert new
        conn.execute("DELETE FROM linkedin_tokens")
        conn.execute(
            """
            INSERT INTO linkedin_tokens
                (access_token, refresh_token, expires_at, person_id, person_name, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            """,
            (access_token, refresh_token, expires_at, person_id, person_name),
        )
        conn.commit()
    finally:
        conn.close()


def _get_token() -> dict[str, Any] | None:
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM linkedin_tokens ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ── Router ────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/integrations/linkedin", tags=["integrations"])

_ensure_table()


@router.get("/status")
def linkedin_status() -> dict[str, Any]:
    """Return whether a LinkedIn token is stored and who it belongs to."""
    row = _get_token()
    if not row:
        return {"connected": False, "user_name": None}
    return {"connected": True, "user_name": row.get("person_name")}


@router.get("/auth")
def linkedin_auth(redirect_after: str = "") -> RedirectResponse:
    """Redirect the browser to LinkedIn's OAuth2 authorization page."""
    if not _CLIENT_ID:
        raise HTTPException(
            status_code=503,
            detail="LINKEDIN_CLIENT_ID is not configured. Set it in .env.",
        )

    # Embed redirect_after into state so callback can send the user back there
    csrf = secrets.token_urlsafe(16)
    state = f"{csrf}|{redirect_after}" if redirect_after else csrf
    _pending_states.add(state)

    params = {
        "response_type": "code",
        "client_id": _CLIENT_ID,
        "redirect_uri": _REDIRECT_URI,
        "state": state,
        "scope": _SCOPES,
    }
    qs = "&".join(f"{k}={requests.utils.quote(str(v))}" for k, v in params.items())
    return RedirectResponse(url=f"{_AUTH_URL}?{qs}")


@router.get("/callback")
def linkedin_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
) -> RedirectResponse:
    """Exchange the authorization code for an access token and store it."""
    if error:
        msg = error_description or error
        return RedirectResponse(url=f"{_FRONTEND_URL}?linkedin_error={requests.utils.quote(msg)}")

    # CSRF check
    if state not in _pending_states:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    _pending_states.discard(state)

    # Parse redirect_after from composite state
    redirect_after = ""
    if "|" in state:
        _, redirect_after = state.split("|", 1)

    # Exchange code → access token
    resp = requests.post(
        _TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  _REDIRECT_URI,
            "client_id":     _CLIENT_ID,
            "client_secret": _CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    if not resp.ok:
        raise HTTPException(
            status_code=502,
            detail=f"LinkedIn token exchange failed: {resp.status_code} {resp.text}",
        )

    token_data = resp.json()
    access_token  = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token")
    expires_in    = token_data.get("expires_in")
    expires_at    = None
    if expires_in:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
        ).isoformat()

    # Fetch user identity via OpenID Connect userinfo
    user_id   = None
    user_name = None
    me_resp = requests.get(
        _ME_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if me_resp.ok:
        me = me_resp.json()
        # `sub` is the stable LinkedIn person ID used in author URNs
        user_id   = me.get("sub")
        given     = me.get("given_name", "")
        family    = me.get("family_name", "")
        user_name = f"{given} {family}".strip() or me.get("name") or me.get("email")

    _upsert_token(access_token, refresh_token, expires_at, user_id, user_name)

    destination = redirect_after if redirect_after else _FRONTEND_URL
    return RedirectResponse(url=destination)


class LinkedInPostRequest(BaseModel):
    text: str
    task_id: str | None = None


@router.post("/post")
def linkedin_post(body: LinkedInPostRequest) -> dict[str, Any]:
    """Publish a text post to LinkedIn using the stored access token."""
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="Post text cannot be empty.")

    row = _get_token()
    if not row:
        raise HTTPException(
            status_code=401,
            detail="LinkedIn is not connected. Authenticate via /api/integrations/linkedin/auth.",
        )

    access_token = row["access_token"]
    user_id      = row.get("person_id")

    if not user_id:
        raise HTTPException(
            status_code=400,
            detail="LinkedIn user ID is missing. Re-authenticate to refresh your credentials.",
        )

    author_urn = f"urn:li:person:{user_id}"

    payload = {
        "author": author_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": body.text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    resp = requests.post(
        _POST_URL,
        json=payload,
        headers={
            "Authorization":             f"Bearer {access_token}",
            "Content-Type":              "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        },
        timeout=15,
    )

    if not resp.ok:
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"LinkedIn API error {resp.status_code}: {resp.text}",
        )

    # LinkedIn returns the post URN in the X-RestLi-Id response header
    post_id = resp.headers.get("x-restli-id") or resp.headers.get("X-RestLi-Id", "")

    return {
        "published": True,
        "post_id":   post_id,
        "author":    author_urn,
    }
