# LinkedIn Publishing Adapter
# Uses the LinkedIn Community Management API (v2 / REST) via urllib — no SDK required.
#
# Required OAuth 2.0 scopes:
#   w_member_social  — create, update, delete posts on behalf of the authenticated member
#   r_member_social  — read post analytics / engagement metrics
#
# How to get an access token:
#   1. Create a LinkedIn App at https://www.linkedin.com/developers/apps
#   2. Under "Products", request "Share on LinkedIn" and "Marketing Developer Platform"
#      (grants w_member_social and r_member_social respectively)
#   3. Add your redirect URI under "Auth" settings
#   4. Direct your user to the OAuth 2.0 authorization URL:
#        https://www.linkedin.com/oauth/v2/authorization
#          ?response_type=code
#          &client_id=<YOUR_CLIENT_ID>
#          &redirect_uri=<YOUR_REDIRECT_URI>
#          &scope=w_member_social%20r_member_social
#   5. After the user grants permission, LinkedIn redirects to your URI with ?code=<AUTH_CODE>
#   6. Exchange the auth code for tokens via the token endpoint (see refresh_token() below
#      — use grant_type=authorization_code for the initial exchange)
#   7. Store the access_token (valid ~60 days) and refresh_token (valid ~1 year)
#   8. Call refresh_token() before the access token expires to get a new one
#
# API reference:
#   Posts (create): https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
#   Share statistics: https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/share-statistics-api
#   Token refresh: https://learn.microsoft.com/en-us/linkedin/shared/authentication/token-introspection

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

_API_BASE = "https://api.linkedin.com"
_TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"
_POSTS_URL = f"{_API_BASE}/rest/posts"
_USERINFO_URL = f"{_API_BASE}/v2/userinfo"


def _json_request(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str],
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make a JSON HTTP request and return the parsed response body."""
    data: bytes | None = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


def _auth_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": "202504",
        "X-Restli-Protocol-Version": "2.0.0",
    }


def _get_member_urn(access_token: str) -> str:
    """Resolve the authenticated member's URN (urn:li:person:<id>)."""
    info = _json_request(_USERINFO_URL, headers=_auth_headers(access_token))
    sub = info.get("sub")
    if not sub:
        raise ValueError(f"Could not resolve member URN from userinfo: {info}")
    return f"urn:li:person:{sub}"


def post_to_linkedin(
    access_token: str,
    text: str,
    image_url: str | None = None,
) -> dict[str, str]:
    """Post to LinkedIn on behalf of the authenticated member.

    Args:
        access_token: A valid OAuth2 access token with w_member_social scope.
        text: The post body text (max ~3000 characters for personal posts).
        image_url: Optional publicly accessible image URL to attach.
                   LinkedIn will download and host the image. For full media
                   upload support (large images, documents) use the Assets API
                   separately; this parameter handles simple inline images via
                   the content.media block.

    Returns:
        {"post_id": "<urn>", "post_url": "https://www.linkedin.com/feed/update/<urn>"}

    Raises:
        urllib.error.HTTPError: on 4xx/5xx responses from LinkedIn.
        ValueError: if the response is missing the expected post ID header.
    """
    author = _get_member_urn(access_token)

    payload: dict[str, Any] = {
        "author": author,
        "commentary": text,
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }

    if image_url:
        payload["content"] = {
            "media": {
                "altText": "",
                "id": image_url,  # direct URL; for uploaded assets use the asset URN
            }
        }

    headers = {
        **_auth_headers(access_token),
        "Content-Type": "application/json",
    }

    request = urllib.request.Request(
        _POSTS_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        # LinkedIn returns the post URN in the X-RestLi-Id header, body is empty
        post_urn = response.headers.get("x-restli-id") or response.headers.get("X-RestLi-Id")
        if not post_urn:
            body = response.read().decode("utf-8", errors="replace")
            raise ValueError(f"LinkedIn did not return a post URN. Response body: {body}")

    encoded_urn = urllib.parse.quote(post_urn, safe="")
    post_url = f"https://www.linkedin.com/feed/update/{encoded_urn}"
    return {"post_id": post_urn, "post_url": post_url}


def get_post_metrics(access_token: str, post_id: str) -> dict[str, int]:
    """Fetch engagement metrics for a published post.

    Args:
        access_token: A valid OAuth2 access token with r_member_social scope.
        post_id: The post URN returned by post_to_linkedin() (e.g. "urn:li:share:123").

    Returns:
        {
            "likes": int,
            "comments": int,
            "reposts": int,
            "impressions": int,
        }

    Raises:
        urllib.error.HTTPError: on 4xx/5xx responses from LinkedIn.
    """
    encoded_urn = urllib.parse.quote(post_id, safe="")
    url = (
        f"{_API_BASE}/rest/socialMetadata/{encoded_urn}"
        "?projection=(totalSocialActivityCounts)"
    )
    data = _json_request(url, headers=_auth_headers(access_token))

    counts = data.get("totalSocialActivityCounts", {})
    return {
        "likes": counts.get("numLikes", 0),
        "comments": counts.get("numComments", 0),
        "reposts": counts.get("numShares", 0),
        "impressions": counts.get("numImpressions", 0),
    }


def refresh_token(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict[str, Any]:
    """Exchange a refresh token for a new access token.

    LinkedIn refresh tokens are valid for ~1 year. Call this before the
    access token (60-day TTL) expires to maintain uninterrupted access.

    Args:
        client_id: Your LinkedIn App's client ID.
        client_secret: Your LinkedIn App's client secret.
        refresh_token: The refresh token from the original OAuth2 flow.

    Returns:
        {
            "access_token": str,
            "expires_in": int,         # seconds until new access token expires
            "refresh_token": str,      # updated refresh token (rotate and store)
            "refresh_token_expires_in": int,
        }

    Raises:
        urllib.error.HTTPError: on 4xx/5xx responses from LinkedIn.
    """
    payload = urllib.parse.urlencode(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        _TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return json.loads(raw)
