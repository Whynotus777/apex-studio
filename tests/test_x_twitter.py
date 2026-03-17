"""
Tests for adapters/publishers/x_twitter.py

Verifies:
  1. Module imports cleanly (compile check)
  2. OAuth 1.0a signature output is deterministic and correctly formed
  3. post_tweet raises ValueError on oversized text
  4. post_thread raises ValueError on empty list
  5. post_thread chains reply_to correctly (via mock)

No live API calls are made. All network I/O is patched.
"""

import base64
import hashlib
import hmac
import importlib
import sys
import unittest
import urllib.parse
import uuid
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# 1. Import / compile check
# ---------------------------------------------------------------------------

class TestImport(unittest.TestCase):
    def test_module_imports(self):
        """Adapter must import without errors."""
        import adapters.publishers.x_twitter as xt
        self.assertTrue(callable(xt.post_tweet))
        self.assertTrue(callable(xt.post_thread))
        self.assertTrue(callable(xt.get_tweet_metrics))


# ---------------------------------------------------------------------------
# 2. OAuth 1.0a signature correctness
# ---------------------------------------------------------------------------

class TestOAuthSignature(unittest.TestCase):
    """
    Re-implement the signing algorithm independently and compare outputs.
    If both implementations agree, the signature logic is internally consistent.
    """

    _API_KEY        = "test_api_key"
    _API_SECRET     = "test_api_secret"
    _ACCESS_TOKEN   = "test_access_token"
    _ACCESS_SECRET  = "test_access_secret"
    _URL            = "https://api.twitter.com/2/tweets"
    _METHOD         = "POST"

    def _reference_sign(
        self,
        method: str,
        url: str,
        oauth_params: dict,
        api_secret: str,
        access_secret: str,
    ) -> str:
        """Independent HMAC-SHA1 reference implementation."""
        def pct(v: str) -> str:
            return urllib.parse.quote(str(v), safe="")

        param_string = "&".join(
            f"{pct(k)}={pct(v)}"
            for k, v in sorted(oauth_params.items())
        )
        base_string = "&".join([pct(method.upper()), pct(url), pct(param_string)])
        signing_key = f"{pct(api_secret)}&{pct(access_secret)}"
        raw = hmac.new(
            signing_key.encode("utf-8"),
            base_string.encode("utf-8"),
            hashlib.sha1,
        ).digest()
        return base64.b64encode(raw).decode("utf-8")

    def test_signature_is_deterministic_for_fixed_nonce(self):
        """
        Given a fixed nonce and timestamp, the adapter must produce the same
        signature as our reference implementation.
        """
        import adapters.publishers.x_twitter as xt

        fixed_nonce = "abc123nonce"
        fixed_ts    = "1700000000"

        oauth_params = {
            "oauth_consumer_key":     self._API_KEY,
            "oauth_nonce":            fixed_nonce,
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_timestamp":        fixed_ts,
            "oauth_token":            self._ACCESS_TOKEN,
            "oauth_version":          "1.0",
        }

        expected_sig = self._reference_sign(
            method      = self._METHOD,
            url         = self._URL,
            oauth_params= oauth_params,
            api_secret  = self._API_SECRET,
            access_secret = self._ACCESS_SECRET,
        )

        # Patch uuid and time so the adapter uses our fixed values
        with patch("adapters.publishers.x_twitter.uuid.uuid4") as mock_uuid, \
             patch("adapters.publishers.x_twitter.time.time") as mock_time:

            mock_uuid.return_value.hex = fixed_nonce
            mock_time.return_value = int(fixed_ts)

            header = xt._build_oauth_header(
                method       = self._METHOD,
                url          = self._URL,
                api_key      = self._API_KEY,
                api_secret   = self._API_SECRET,
                access_token = self._ACCESS_TOKEN,
                access_secret= self._ACCESS_SECRET,
            )

        self.assertIn(f'oauth_signature="{urllib.parse.quote(expected_sig, safe="")}"', header)

    def test_header_contains_required_oauth_fields(self):
        """Authorization header must declare all required OAuth 1.0a fields."""
        import adapters.publishers.x_twitter as xt

        header = xt._build_oauth_header(
            method        = "GET",
            url           = self._URL,
            api_key       = self._API_KEY,
            api_secret    = self._API_SECRET,
            access_token  = self._ACCESS_TOKEN,
            access_secret = self._ACCESS_SECRET,
        )

        self.assertTrue(header.startswith("OAuth "))
        for field in (
            "oauth_consumer_key",
            "oauth_nonce",
            "oauth_signature",
            "oauth_signature_method",
            "oauth_timestamp",
            "oauth_token",
            "oauth_version",
        ):
            self.assertIn(field, header, f"Missing OAuth field: {field}")

    def test_different_nonces_produce_different_signatures(self):
        """Each call should produce a unique nonce → unique signature."""
        import adapters.publishers.x_twitter as xt

        h1 = xt._build_oauth_header("POST", self._URL, self._API_KEY, self._API_SECRET, self._ACCESS_TOKEN, self._ACCESS_SECRET)
        h2 = xt._build_oauth_header("POST", self._URL, self._API_KEY, self._API_SECRET, self._ACCESS_TOKEN, self._ACCESS_SECRET)
        # Nonces differ between calls so signatures should differ
        self.assertNotEqual(h1, h2)


# ---------------------------------------------------------------------------
# 3. Input validation
# ---------------------------------------------------------------------------

class TestInputValidation(unittest.TestCase):
    _CREDS = ("k", "s", "t", "ts")

    def test_post_tweet_rejects_text_over_280_chars(self):
        import adapters.publishers.x_twitter as xt
        with self.assertRaises(ValueError):
            xt.post_tweet(*self._CREDS, text="x" * 281)

    def test_post_tweet_accepts_exactly_280_chars(self):
        """280-char text must reach the network layer, not raise."""
        import adapters.publishers.x_twitter as xt

        fake_response = MagicMock()
        fake_response.read.return_value = b'{"data": {"id": "1"}}'
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_response):
            result = xt.post_tweet(*self._CREDS, text="x" * 280)
        self.assertEqual(result["tweet_id"], "1")

    def test_post_thread_rejects_empty_list(self):
        import adapters.publishers.x_twitter as xt
        with self.assertRaises(ValueError):
            xt.post_thread(*self._CREDS, tweets=[])


# ---------------------------------------------------------------------------
# 4. Thread chaining
# ---------------------------------------------------------------------------

class TestThreadChaining(unittest.TestCase):
    _CREDS = ("k", "s", "t", "ts")

    def test_thread_chains_reply_to_correctly(self):
        """
        Each tweet after the first must pass the previous tweet's ID as
        reply_to_tweet_id to post_tweet.
        """
        import adapters.publishers.x_twitter as xt

        call_args_list = []

        def fake_post_tweet(*args, **kwargs):
            # Return incrementing IDs
            idx = len(call_args_list) + 1
            call_args_list.append(kwargs)
            return {"tweet_id": str(idx), "tweet_url": f"https://x.com/i/web/status/{idx}"}

        with patch("adapters.publishers.x_twitter.post_tweet", side_effect=fake_post_tweet):
            ids = xt.post_thread(*self._CREDS, tweets=["first", "second", "third"])

        self.assertEqual(ids, ["1", "2", "3"])
        self.assertIsNone(call_args_list[0].get("reply_to_tweet_id"))
        self.assertEqual(call_args_list[1]["reply_to_tweet_id"], "1")
        self.assertEqual(call_args_list[2]["reply_to_tweet_id"], "2")

    def test_single_tweet_thread_returns_one_id(self):
        import adapters.publishers.x_twitter as xt

        fake_response = MagicMock()
        fake_response.read.return_value = b'{"data": {"id": "99"}}'
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_response):
            ids = xt.post_thread(*self._CREDS, tweets=["only tweet"])
        self.assertEqual(ids, ["99"])


# ---------------------------------------------------------------------------
# 5. Metrics parsing
# ---------------------------------------------------------------------------

class TestMetricsParsing(unittest.TestCase):
    _CREDS = ("k", "s", "t", "ts")

    def test_get_tweet_metrics_parses_public_metrics(self):
        import adapters.publishers.x_twitter as xt

        payload = {
            "data": {
                "id": "42",
                "text": "hello world",
                "public_metrics": {
                    "like_count": 10,
                    "retweet_count": 5,
                    "reply_count": 2,
                    "quote_count": 1,
                },
            }
        }
        import json as _json

        fake_response = MagicMock()
        fake_response.read.return_value = _json.dumps(payload).encode()
        fake_response.__enter__ = lambda s: s
        fake_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_response):
            metrics = xt.get_tweet_metrics(*self._CREDS, tweet_id="42")

        self.assertEqual(metrics["tweet_id"], "42")
        self.assertEqual(metrics["like_count"], 10)
        self.assertEqual(metrics["retweet_count"], 5)
        self.assertEqual(metrics["reply_count"], 2)
        self.assertEqual(metrics["quote_count"], 1)
        self.assertEqual(metrics["text"], "hello world")


if __name__ == "__main__":
    unittest.main()
