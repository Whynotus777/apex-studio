"""
X (Twitter) publishing adapter — OAuth 1.0a, X API v2.

Free tier limits (as of 2024):
  - 500 posts/month per app (~17/day)
  - 1 app per developer account
  - Read + Write permissions required

Keys you need from developer.x.com:
  1. Go to developer.x.com → sign in → "Sign up for Free Account"
  2. Create a Project and an App inside it
  3. Under "App settings" → "User authentication settings":
       - Enable OAuth 1.0a
       - Set App permissions to "Read and Write"
  4. Copy four values from "Keys and Tokens":
       - API Key             (also called Consumer Key)
       - API Key Secret      (also called Consumer Secret)
       - Access Token        (generated for your own account)
       - Access Token Secret (generated for your own account)

Pass all four to every function in this module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
import urllib.request
import uuid
from typing import Any

# ---------------------------------------------------------------------------
# X API v2 endpoints
# ---------------------------------------------------------------------------

_TWEETS_URL = "https://api.twitter.com/2/tweets"
_TWEET_METRICS_URL = "https://api.twitter.com/2/tweets/{tweet_id}"


# ---------------------------------------------------------------------------
# OAuth 1.0a signing
# ---------------------------------------------------------------------------

def _percent_encode(value: str) -> str:
    """RFC 3986 percent-encoding (urllib.parse.quote with safe='')."""
    return urllib.parse.quote(str(value), safe="")


def _build_oauth_header(
    method: str,
    url: str,
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    extra_oauth_params: dict[str, str] | None = None,
) -> str:
    """
    Build the OAuth 1.0a Authorization header for a request.

    Implements the standard HMAC-SHA1 signature method:
      1. Collect oauth_* parameters + request parameters
      2. Sort and percent-encode into a parameter string
      3. Build the signature base string
      4. Derive the signing key from consumer secret + token secret
      5. HMAC-SHA1 sign and base64-encode
      6. Assemble the Authorization header
    """
    oauth_params: dict[str, str] = {
        "oauth_consumer_key": api_key,
        "oauth_nonce": uuid.uuid4().hex,
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": str(int(time.time())),
        "oauth_token": access_token,
        "oauth_version": "1.0",
    }
    if extra_oauth_params:
        oauth_params.update(extra_oauth_params)

    # Parameter string: all oauth params sorted by key
    param_string = "&".join(
        f"{_percent_encode(k)}={_percent_encode(v)}"
        for k, v in sorted(oauth_params.items())
    )

    # Signature base string
    base_string = "&".join([
        _percent_encode(method.upper()),
        _percent_encode(url),
        _percent_encode(param_string),
    ])

    # Signing key
    signing_key = f"{_percent_encode(api_secret)}&{_percent_encode(access_secret)}"

    # HMAC-SHA1 signature
    raw_signature = hmac.new(
        signing_key.encode("utf-8"),
        base_string.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    signature = base64.b64encode(raw_signature).decode("utf-8")

    oauth_params["oauth_signature"] = signature

    # Assemble header
    header_parts = ", ".join(
        f'{_percent_encode(k)}="{_percent_encode(v)}"'
        for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_parts}"


# ---------------------------------------------------------------------------
# Low-level HTTP helper
# ---------------------------------------------------------------------------

def _api_request(
    method: str,
    url: str,
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    body: dict[str, Any] | None = None,
    query_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Make a signed request to the X API and return parsed JSON.

    Raises RuntimeError on HTTP errors with the response body included.
    """
    full_url = url
    if query_params:
        full_url = f"{url}?{urllib.parse.urlencode(query_params)}"

    auth_header = _build_oauth_header(
        method=method,
        url=url,  # Signature uses base URL without query string
        api_key=api_key,
        api_secret=api_secret,
        access_token=access_token,
        access_secret=access_secret,
    )

    encoded_body: bytes | None = None
    headers: dict[str, str] = {"Authorization": auth_header}

    if body is not None:
        encoded_body = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(
        full_url,
        data=encoded_body,
        headers=headers,
        method=method.upper(),
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.request.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"X API {method} {url} → HTTP {exc.code}: {raw}"
        ) from exc

    return json.loads(raw)


# ---------------------------------------------------------------------------
# Media upload (v1.1 — v2 does not support media upload directly)
# ---------------------------------------------------------------------------

_MEDIA_UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"


def _upload_media(
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    media_path: str,
) -> str:
    """
    Upload a media file via the v1.1 media/upload endpoint.

    Returns the media_id_string to attach to a tweet.
    Only images (JPEG, PNG, GIF, WEBP) are supported on the free tier.
    """
    with open(media_path, "rb") as fh:
        media_data = base64.b64encode(fh.read()).decode("utf-8")

    body = urllib.parse.urlencode({"media_data": media_data}).encode("utf-8")

    auth_header = _build_oauth_header(
        method="POST",
        url=_MEDIA_UPLOAD_URL,
        api_key=api_key,
        api_secret=api_secret,
        access_token=access_token,
        access_secret=access_secret,
    )

    req = urllib.request.Request(
        _MEDIA_UPLOAD_URL,
        data=body,
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.request.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"X media upload → HTTP {exc.code}: {raw}"
        ) from exc

    return result["media_id_string"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def post_tweet(
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    text: str,
    media_path: str | None = None,
    reply_to_tweet_id: str | None = None,
) -> dict[str, str]:
    """
    Post a single tweet via the X API v2.

    Args:
        api_key:            API Key (Consumer Key) from developer.x.com
        api_secret:         API Key Secret (Consumer Secret)
        access_token:       Access Token for the posting account
        access_secret:      Access Token Secret
        text:               Tweet text (max 280 characters)
        media_path:         Optional local file path to attach as media
        reply_to_tweet_id:  If set, posts as a reply to this tweet ID

    Returns:
        {"tweet_id": "...", "tweet_url": "https://x.com/i/web/status/<id>"}

    Free tier limit: ~500 posts/month (~17/day).
    """
    if len(text) > 280:
        raise ValueError(f"Tweet text exceeds 280 characters ({len(text)})")

    body: dict[str, Any] = {"text": text}

    if media_path:
        media_id = _upload_media(
            api_key, api_secret, access_token, access_secret, media_path
        )
        body["media"] = {"media_ids": [media_id]}

    if reply_to_tweet_id:
        body["reply"] = {"in_reply_to_tweet_id": reply_to_tweet_id}

    result = _api_request(
        method="POST",
        url=_TWEETS_URL,
        api_key=api_key,
        api_secret=api_secret,
        access_token=access_token,
        access_secret=access_secret,
        body=body,
    )

    tweet_id = result["data"]["id"]
    return {
        "tweet_id": tweet_id,
        "tweet_url": f"https://x.com/i/web/status/{tweet_id}",
    }


def post_thread(
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    tweets: list[str],
) -> list[str]:
    """
    Post a thread by chaining tweets as sequential replies.

    Args:
        api_key:       API Key from developer.x.com
        api_secret:    API Key Secret
        access_token:  Access Token
        access_secret: Access Token Secret
        tweets:        Ordered list of tweet texts. First tweet is the thread root.

    Returns:
        List of tweet IDs in posting order.

    Note: Each tweet in the thread counts against the 500/month limit.
    """
    if not tweets:
        raise ValueError("tweets list must not be empty")

    tweet_ids: list[str] = []
    reply_to: str | None = None

    for text in tweets:
        result = post_tweet(
            api_key=api_key,
            api_secret=api_secret,
            access_token=access_token,
            access_secret=access_secret,
            text=text,
            reply_to_tweet_id=reply_to,
        )
        tweet_id = result["tweet_id"]
        tweet_ids.append(tweet_id)
        reply_to = tweet_id

    return tweet_ids


def get_tweet_metrics(
    api_key: str,
    api_secret: str,
    access_token: str,
    access_secret: str,
    tweet_id: str,
) -> dict[str, Any]:
    """
    Fetch engagement metrics for a tweet via the X API v2.

    Args:
        api_key:       API Key from developer.x.com
        api_secret:    API Key Secret
        access_token:  Access Token
        access_secret: Access Token Secret
        tweet_id:      The ID string of the tweet to look up

    Returns:
        Dict with keys: tweet_id, text, like_count, retweet_count,
        reply_count, quote_count, impression_count (where available).

    Note: Public metrics (likes, retweets, replies, quotes) are available
    on the free tier. Non-public metrics (impressions) require Basic tier+.
    """
    url = _TWEET_METRICS_URL.format(tweet_id=tweet_id)
    result = _api_request(
        method="GET",
        url=url,
        api_key=api_key,
        api_secret=api_secret,
        access_token=access_token,
        access_secret=access_secret,
        query_params={
            "tweet.fields": "public_metrics,non_public_metrics",
        },
    )

    data = result.get("data", {})
    metrics = data.get("public_metrics", {})
    non_public = data.get("non_public_metrics", {})

    return {
        "tweet_id": tweet_id,
        "text": data.get("text", ""),
        "like_count": metrics.get("like_count", 0),
        "retweet_count": metrics.get("retweet_count", 0),
        "reply_count": metrics.get("reply_count", 0),
        "quote_count": metrics.get("quote_count", 0),
        "impression_count": non_public.get("impression_count", metrics.get("impression_count", 0)),
    }
