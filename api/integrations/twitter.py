"""
twitter.py — X/Twitter OAuth2 PKCE integration for APEX.

Flow:
  1. GET /api/integrations/twitter/auth?workspace_id=...&return_url=...
     → generates PKCE verifier+challenge, stores state in DB, returns auth_url
  2. User authorizes at Twitter → callback: GET /api/integrations/twitter/callback?code=...&state=...
     → exchanges code for access_token, stores token, redirects to return_url
  3. POST /api/integrations/twitter/post
     → tweets using stored access_token (truncated to 280 chars if needed)

Requires env vars:
  TWITTER_CLIENT_ID      — OAuth2 App client_id from developer.twitter.com
  TWITTER_CLIENT_SECRET  — client_secret (confidential client flow)
  TWITTER_REDIRECT_URI   — must match URI registered in the app settings
                           default: http://localhost:8000/api/integrations/twitter/callback
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests as _requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

try:
    import tweepy  # type: ignore[import]
except ImportError:
    tweepy = None  # type: ignore[assignment]

TWITTER_CLIENT_ID     = os.environ.get("TWITTER_CLIENT_ID", "")
TWITTER_CLIENT_SECRET = os.environ.get("TWITTER_CLIENT_SECRET", "")
TWITTER_REDIRECT_URI  = os.environ.get(
    "TWITTER_REDIRECT_URI",
    "http://localhost:8000/api/integrations/twitter/callback",
)
TWITTER_SCOPES = ["tweet.write", "tweet.read", "users.read", "offline.access"]

_TWEET_LIMIT = 280  # Twitter hard cap


class TwitterIntegration:
    """Manages X/Twitter OAuth2 PKCE tokens and posting per workspace."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._migrate()

    # ── OAuth flow ──────────────────────────────────────────────────────

    def get_auth_url(
        self,
        workspace_id: str | None = None,
        return_url: str | None = None,
    ) -> dict[str, str]:
        """
        Generate a Twitter OAuth2 PKCE authorization URL.

        Stores (state, code_verifier, workspace_id, return_url) in the
        integrations table so handle_callback() can complete the exchange.

        Returns:
            {auth_url: str, state: str}
        """
        if not TWITTER_CLIENT_ID:
            raise ValueError(
                "TWITTER_CLIENT_ID is not set. Add it to your .env file."
            )

        # PKCE: code_verifier is a random secret; challenge is its base64url(SHA-256)
        code_verifier = secrets.token_urlsafe(64)
        code_challenge = (
            base64.urlsafe_b64encode(
                hashlib.sha256(code_verifier.encode()).digest()
            )
            .rstrip(b"=")
            .decode()
        )
        state = secrets.token_urlsafe(32)

        auth_url = "https://twitter.com/i/oauth2/authorize?" + urlencode(
            {
                "response_type":         "code",
                "client_id":             TWITTER_CLIENT_ID,
                "redirect_uri":          TWITTER_REDIRECT_URI,
                "scope":                 " ".join(TWITTER_SCOPES),
                "state":                 state,
                "code_challenge":        code_challenge,
                "code_challenge_method": "S256",
            }
        )

        meta = json.dumps({"return_url": return_url or "/"})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO integrations
                    (provider, workspace_id, pkce_verifier, pkce_state, meta,
                     created_at, updated_at)
                VALUES ('twitter', ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (workspace_id, code_verifier, state, meta),
            )
            conn.commit()

        return {"auth_url": auth_url, "state": state}

    def handle_callback(self, code: str, state: str) -> dict[str, Any]:
        """
        Exchange authorization code for an access token.

        Called by the /callback endpoint after Twitter redirects the user back.

        Returns:
            {workspace_id, connected: True, return_url}
        """
        if not TWITTER_CLIENT_ID:
            raise ValueError("TWITTER_CLIENT_ID is not set.")

        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT rowid, workspace_id, pkce_verifier, meta
                FROM integrations
                WHERE pkce_state = ? AND provider = 'twitter'
                ORDER BY rowid DESC LIMIT 1
                """,
                (state,),
            ).fetchone()

        if not row:
            raise ValueError(
                "Invalid or expired OAuth state. Please start the auth flow again."
            )

        rowid        = row["rowid"]
        workspace_id = row["workspace_id"]
        verifier     = row["pkce_verifier"]
        meta         = json.loads(row["meta"] or "{}")
        return_url   = meta.get("return_url", "/")

        # Exchange code for token
        auth = (TWITTER_CLIENT_ID, TWITTER_CLIENT_SECRET) if TWITTER_CLIENT_SECRET else None
        resp = _requests.post(
            "https://api.twitter.com/2/oauth2/token",
            data={
                "code":          code,
                "grant_type":    "authorization_code",
                "redirect_uri":  TWITTER_REDIRECT_URI,
                "code_verifier": verifier,
                "client_id":     TWITTER_CLIENT_ID,
            },
            auth=auth,
            timeout=30,
        )
        if not resp.ok:
            raise ValueError(
                f"Token exchange failed ({resp.status_code}): {resp.text}"
            )

        td            = resp.json()
        access_token  = td.get("access_token", "")
        refresh_token = td.get("refresh_token")
        expires_in    = td.get("expires_in", 7200)
        expires_at    = (
            datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        ).isoformat()
        scope = td.get("scope", "")

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE integrations
                SET access_token  = ?,
                    refresh_token = ?,
                    token_type    = 'bearer',
                    expires_at    = ?,
                    scope         = ?,
                    pkce_verifier = NULL,
                    pkce_state    = NULL,
                    updated_at    = datetime('now')
                WHERE rowid = ?
                """,
                (access_token, refresh_token, expires_at, scope, rowid),
            )
            conn.commit()

        return {
            "workspace_id": workspace_id,
            "connected":    True,
            "return_url":   return_url,
        }

    # ── Posting ─────────────────────────────────────────────────────────

    def post_tweet(self, workspace_id: str, content: str) -> dict[str, Any]:
        """
        Post a tweet using the stored access token for this workspace.

        Content is truncated to 280 chars at a word boundary if necessary.

        Returns:
            {tweet_id, tweet_url, content}
        """
        token = self.get_token(workspace_id)
        if not token:
            raise _NotConnectedError(
                "Twitter is not connected for this workspace. "
                "Please connect your X account first."
            )

        access_token = token["access_token"]
        tweet_text   = _truncate_tweet(content)

        if tweepy is not None:
            client = tweepy.Client(access_token=access_token)
            try:
                response = client.create_tweet(text=tweet_text)
                tweet_id = response.data["id"]
            except tweepy.errors.Unauthorized as exc:
                raise ValueError(
                    "Twitter token is invalid or expired. Please reconnect."
                ) from exc
            except tweepy.errors.Forbidden as exc:
                raise ValueError(f"Twitter rejected the tweet: {exc}") from exc
            except Exception as exc:
                raise ValueError(f"Failed to post tweet: {exc}") from exc
        else:
            # tweepy not installed — fall back to direct requests
            resp = _requests.post(
                "https://api.twitter.com/2/tweets",
                json={"text": tweet_text},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type":  "application/json",
                },
                timeout=30,
            )
            if resp.status_code == 401:
                raise ValueError(
                    "Twitter token is invalid or expired. Please reconnect."
                )
            if not resp.ok:
                raise ValueError(
                    f"Tweet failed ({resp.status_code}): {resp.text}"
                )
            tweet_id = resp.json().get("data", {}).get("id", "")

        return {
            "tweet_id":  tweet_id,
            "tweet_url": f"https://twitter.com/i/web/status/{tweet_id}",
            "content":   tweet_text,
        }

    # ── Token management ────────────────────────────────────────────────

    def get_token(self, workspace_id: str) -> dict[str, Any] | None:
        """Return the stored access token for a workspace, or None.

        Falls back to any twitter token if no workspace-specific one exists,
        supporting the common single-user setup.
        """
        with self._connect() as conn:
            # Try workspace-specific first, then global fallback
            row = conn.execute(
                """
                SELECT access_token, refresh_token, expires_at, scope
                FROM integrations
                WHERE provider = 'twitter' AND access_token IS NOT NULL
                  AND (workspace_id = ? OR workspace_id IS NULL)
                ORDER BY (workspace_id = ?) DESC, updated_at DESC
                LIMIT 1
                """,
                (workspace_id, workspace_id),
            ).fetchone()
        return dict(row) if row else None

    def is_connected(self, workspace_id: str) -> bool:
        """Return True if the workspace has a stored Twitter access token."""
        return self.get_token(workspace_id) is not None

    # ── DB setup ────────────────────────────────────────────────────────

    def _migrate(self) -> None:
        """Ensure integrations table has the columns this class needs.

        Uses ALTER TABLE ADD COLUMN (idempotent) to extend an existing
        table rather than recreating it.
        """
        extra_cols = [
            ("workspace_id",  "TEXT"),
            ("pkce_verifier", "TEXT"),
            ("pkce_state",    "TEXT"),
            ("scope",         "TEXT"),
            ("token_type",    "TEXT"),
            ("refresh_token", "TEXT"),
            ("expires_at",    "TEXT"),
        ]
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            # Ensure the base table exists (matches the schema already in schema.sql)
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS integrations (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider      TEXT NOT NULL,
                    access_token  TEXT,
                    refresh_token TEXT,
                    expires_at    TEXT,
                    meta          TEXT DEFAULT '{}',
                    created_at    TEXT DEFAULT (datetime('now')),
                    updated_at    TEXT DEFAULT (datetime('now'))
                )
                """
            )
            # Add missing columns idempotently
            for col_name, col_type in extra_cols:
                try:
                    conn.execute(
                        f"ALTER TABLE integrations ADD COLUMN {col_name} {col_type}"
                    )
                except sqlite3.OperationalError:
                    pass  # column already exists
            # Index for fast state lookup during callback
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_integrations_pkce_state "
                "ON integrations(pkce_state)"
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn


# ── Sentinel (caught by endpoint to return 401) ────────────────────────

class _NotConnectedError(ValueError):
    """Raised when a workspace has no Twitter token."""


# ── Helpers ────────────────────────────────────────────────────────────

def _truncate_tweet(text: str) -> str:
    """Truncate text to ≤280 chars at a word boundary."""
    if len(text) <= _TWEET_LIMIT:
        return text
    chunk = text[: _TWEET_LIMIT - 1]
    last_space = chunk.rfind(" ")
    if last_space > _TWEET_LIMIT // 2:
        return chunk[:last_space].rstrip() + "…"
    return chunk + "…"


# ── FastAPI router ──────────────────────────────────────────────────────

_DB_PATH = Path(os.environ.get("APEX_HOME", Path(__file__).parent.parent.parent)) / "db" / "apex_state.db"
_FRONTEND_URL = os.environ.get("TWITTER_FRONTEND_URL", "http://localhost:3000")

router = APIRouter()
_ti = TwitterIntegration(db_path=_DB_PATH)


class _TweetRequest(BaseModel):
    content: str
    workspace_id: str


@router.get("/api/integrations/twitter/status")
def twitter_status(workspace_id: str = Query(default="default")) -> dict[str, Any]:
    """Return Twitter connection status for a workspace."""
    return {
        "connected": _ti.is_connected(workspace_id),
        "user_name": None,  # Twitter API v2 user lookup requires extra scope; skip for now
    }


@router.get("/api/integrations/twitter/auth")
def twitter_auth(
    workspace_id: str = Query(default="default"),
    return_url: str = Query(default="/"),
) -> RedirectResponse:
    """Redirect the user to Twitter's OAuth2 authorization page."""
    if not TWITTER_CLIENT_ID:
        raise HTTPException(status_code=500, detail="TWITTER_CLIENT_ID is not configured.")
    result = _ti.get_auth_url(workspace_id=workspace_id, return_url=return_url)
    return RedirectResponse(url=result["auth_url"])


@router.get("/api/integrations/twitter/callback")
def twitter_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Exchange the authorization code for an access token and redirect to the return URL."""
    if error:
        return RedirectResponse(url=f"{_FRONTEND_URL}?twitter_error={error}")
    try:
        result = _ti.handle_callback(code=code, state=state)
    except ValueError as exc:
        return RedirectResponse(url=f"{_FRONTEND_URL}?twitter_error={str(exc)[:120]}")
    return_url = result.get("return_url") or "/"
    separator = "&" if "?" in return_url else "?"
    return RedirectResponse(url=f"{return_url}{separator}twitter_connected=1")


@router.post("/api/integrations/twitter/post")
def post_to_twitter(payload: _TweetRequest) -> dict[str, Any]:
    """Post a tweet on behalf of the workspace. Returns tweet_id and tweet_url."""
    try:
        return _ti.post_tweet(workspace_id=payload.workspace_id, content=payload.content)
    except _NotConnectedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
