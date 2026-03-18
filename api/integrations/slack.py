from __future__ import annotations

import json
import os
import sqlite3
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

APEX_HOME = Path(__file__).resolve().parents[2]
DB_PATH = APEX_HOME / "db" / "apex_state.db"

router = APIRouter(prefix="/api/integrations/slack", tags=["integrations"])


class SlackSendRequest(BaseModel):
    channel: str = Field(..., min_length=1)
    text: str = Field(..., min_length=1)
    integration_id: str | None = None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def _ensure_integrations_table() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS integrations (
                id TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                state TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                team_id TEXT,
                team_name TEXT,
                access_token TEXT,
                refresh_token TEXT,
                bot_user_id TEXT,
                scope TEXT,
                incoming_webhook_url TEXT,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_integrations_provider ON integrations(provider)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_integrations_state ON integrations(provider, state)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_integrations_status ON integrations(provider, status)"
        )


def _client_config() -> tuple[str, str, str, str]:
    client_id = os.environ.get("SLACK_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SLACK_CLIENT_SECRET", "").strip()
    redirect_uri = os.environ.get("SLACK_REDIRECT_URI", "").strip()
    scopes = os.environ.get("SLACK_SCOPES", "chat:write,channels:read,groups:read")

    if not client_id or not client_secret or not redirect_uri:
        raise HTTPException(
            status_code=500,
            detail=(
                "Slack OAuth is not configured. Set SLACK_CLIENT_ID, "
                "SLACK_CLIENT_SECRET, and SLACK_REDIRECT_URI."
            ),
        )

    return client_id, client_secret, redirect_uri, scopes


def _latest_integration(integration_id: str | None = None) -> sqlite3.Row | None:
    _ensure_integrations_table()
    with _connect() as conn:
        if integration_id:
            row = conn.execute(
                "SELECT * FROM integrations WHERE id = ? AND provider = 'slack'",
                (integration_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """
                SELECT * FROM integrations
                WHERE provider = 'slack' AND status = 'connected' AND access_token IS NOT NULL
                ORDER BY updated_at DESC, created_at DESC
                LIMIT 1
                """,
            ).fetchone()
    return row


@router.get("/auth")
def slack_auth(request: Request) -> RedirectResponse:
    client_id, _, redirect_uri, scopes = _client_config()
    _ensure_integrations_table()

    integration_id = str(uuid.uuid4())
    state = str(uuid.uuid4())
    metadata = {
        "requested_from": str(request.url),
        "redirect_uri": redirect_uri,
    }

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO integrations (id, provider, state, status, metadata)
            VALUES (?, 'slack', ?, 'pending', ?)
            """,
            (integration_id, state, json.dumps(metadata)),
        )

    query = urlencode(
        {
            "client_id": client_id,
            "scope": scopes,
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return RedirectResponse(url=f"https://slack.com/oauth/v2/authorize?{query}", status_code=302)


@router.get("/callback")
def slack_callback(code: str, state: str) -> JSONResponse:
    client_id, client_secret, redirect_uri, _ = _client_config()
    _ensure_integrations_table()

    with _connect() as conn:
        pending = conn.execute(
            "SELECT * FROM integrations WHERE provider = 'slack' AND state = ? LIMIT 1",
            (state,),
        ).fetchone()

    if pending is None:
        raise HTTPException(status_code=400, detail="Invalid or expired Slack OAuth state.")

    client = WebClient()
    try:
        oauth = client.oauth_v2_access(
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )
    except SlackApiError as exc:
        message = exc.response.get("error", str(exc)) if exc.response else str(exc)
        raise HTTPException(status_code=400, detail=f"Slack OAuth failed: {message}") from exc

    team = oauth.get("team") or {}
    incoming_webhook = oauth.get("incoming_webhook") or {}
    authed_user = oauth.get("authed_user") or {}
    metadata = pending["metadata"] or "{}"

    with _connect() as conn:
        conn.execute(
            """
            UPDATE integrations
            SET status = 'connected',
                team_id = ?,
                team_name = ?,
                access_token = ?,
                refresh_token = ?,
                bot_user_id = ?,
                scope = ?,
                incoming_webhook_url = ?,
                metadata = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (
                team.get("id"),
                team.get("name"),
                oauth.get("access_token"),
                oauth.get("refresh_token") or authed_user.get("refresh_token"),
                oauth.get("bot_user_id") or authed_user.get("id"),
                oauth.get("scope"),
                incoming_webhook.get("url"),
                metadata,
                pending["id"],
            ),
        )

    return JSONResponse(
        {
            "ok": True,
            "integration_id": pending["id"],
            "provider": "slack",
            "team_id": team.get("id"),
            "team_name": team.get("name"),
            "scope": oauth.get("scope"),
        }
    )


@router.post("/send")
def slack_send(payload: SlackSendRequest) -> dict[str, Any]:
    integration = _latest_integration(payload.integration_id)
    if integration is None or not integration["access_token"]:
        raise HTTPException(
            status_code=404,
            detail="No connected Slack integration found. Authorize Slack first.",
        )

    client = WebClient(token=integration["access_token"])
    try:
        response = client.chat_postMessage(channel=payload.channel, text=payload.text)
    except SlackApiError as exc:
        message = exc.response.get("error", str(exc)) if exc.response else str(exc)
        raise HTTPException(status_code=400, detail=f"Slack send failed: {message}") from exc

    channel_id = response.get("channel")
    ts = response.get("ts")
    permalink = None
    if channel_id and ts:
        try:
            permalink_resp = client.chat_getPermalink(channel=channel_id, message_ts=ts)
            permalink = permalink_resp.get("permalink")
        except SlackApiError:
            permalink = None

    return {
        "ok": True,
        "integration_id": integration["id"],
        "provider": "slack",
        "channel": channel_id,
        "ts": ts,
        "permalink": permalink,
        "message": {"text": payload.text},
    }
