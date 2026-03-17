"""
Tests for adapters/publishers/linkedin.py

These tests verify module structure and function signatures only.
No real LinkedIn API calls are made.

Required OAuth 2.0 scopes (reminder):
  w_member_social  — create/delete posts on behalf of the member
  r_member_social  — read post engagement metrics

How to get a token (summary):
  1. Create a LinkedIn App → request "Share on LinkedIn" + "Marketing Developer Platform" products
  2. Direct user to authorization URL with scope=w_member_social%20r_member_social
  3. Exchange the returned auth code at /oauth/v2/accessToken
  4. Refresh with refresh_token() before 60-day access token expires
"""

import inspect
import sys
import types
import unittest
import urllib.error
from unittest.mock import MagicMock, patch


class TestLinkedInModuleImport(unittest.TestCase):
    def test_module_imports_without_error(self):
        import adapters.publishers.linkedin as m  # noqa: F401 — import is the assertion

    def test_package_init_imports_all_three_functions(self):
        from adapters.publishers import get_post_metrics, post_to_linkedin, refresh_token

        self.assertTrue(callable(post_to_linkedin))
        self.assertTrue(callable(get_post_metrics))
        self.assertTrue(callable(refresh_token))


class TestPostToLinkedInSignature(unittest.TestCase):
    def setUp(self):
        from adapters.publishers.linkedin import post_to_linkedin

        self.fn = post_to_linkedin
        self.sig = inspect.signature(post_to_linkedin)

    def test_has_access_token_param(self):
        self.assertIn("access_token", self.sig.parameters)

    def test_has_text_param(self):
        self.assertIn("text", self.sig.parameters)

    def test_has_image_url_param_optional(self):
        param = self.sig.parameters.get("image_url")
        self.assertIsNotNone(param)
        self.assertIs(param.default, None)

    def test_returns_dict_with_expected_keys(self):
        """Mock the HTTP layer and verify return shape."""
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.headers = {"x-restli-id": "urn:li:share:9999999"}
        mock_response.read.return_value = b""

        mock_userinfo_response = MagicMock()
        mock_userinfo_response.__enter__ = lambda s: s
        mock_userinfo_response.__exit__ = MagicMock(return_value=False)
        mock_userinfo_response.read.return_value = b'{"sub": "abc123"}'

        call_count = 0

        def fake_urlopen(request, timeout=30):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # first call: userinfo
                return mock_userinfo_response
            # second call: post creation
            return mock_response

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            result = self.fn("fake_token", "Hello LinkedIn!")

        self.assertIsInstance(result, dict)
        self.assertIn("post_id", result)
        self.assertIn("post_url", result)
        self.assertEqual(result["post_id"], "urn:li:share:9999999")
        self.assertIn("linkedin.com", result["post_url"])


class TestGetPostMetricsSignature(unittest.TestCase):
    def setUp(self):
        from adapters.publishers.linkedin import get_post_metrics

        self.fn = get_post_metrics
        self.sig = inspect.signature(get_post_metrics)

    def test_has_access_token_param(self):
        self.assertIn("access_token", self.sig.parameters)

    def test_has_post_id_param(self):
        self.assertIn("post_id", self.sig.parameters)

    def test_returns_dict_with_metric_keys(self):
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = json_bytes(
            {
                "totalSocialActivityCounts": {
                    "numLikes": 42,
                    "numComments": 7,
                    "numShares": 3,
                    "numImpressions": 1500,
                }
            }
        )

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = self.fn("fake_token", "urn:li:share:9999999")

        self.assertIsInstance(result, dict)
        for key in ("likes", "comments", "reposts", "impressions"):
            self.assertIn(key, result)
        self.assertEqual(result["likes"], 42)
        self.assertEqual(result["comments"], 7)
        self.assertEqual(result["reposts"], 3)
        self.assertEqual(result["impressions"], 1500)

    def test_returns_zeros_for_missing_counts(self):
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = b"{}"

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = self.fn("fake_token", "urn:li:share:0")

        self.assertEqual(result["likes"], 0)
        self.assertEqual(result["impressions"], 0)


class TestRefreshTokenSignature(unittest.TestCase):
    def setUp(self):
        from adapters.publishers.linkedin import refresh_token

        self.fn = refresh_token
        self.sig = inspect.signature(refresh_token)

    def test_has_client_id_param(self):
        self.assertIn("client_id", self.sig.parameters)

    def test_has_client_secret_param(self):
        self.assertIn("client_secret", self.sig.parameters)

    def test_has_refresh_token_param(self):
        self.assertIn("refresh_token", self.sig.parameters)

    def test_returns_dict_with_access_token(self):
        mock_response = MagicMock()
        mock_response.__enter__ = lambda s: s
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_response.read.return_value = json_bytes(
            {
                "access_token": "new_access_token_xyz",
                "expires_in": 5183944,
                "refresh_token": "new_refresh_token_xyz",
                "refresh_token_expires_in": 31536000,
            }
        )

        with patch("urllib.request.urlopen", return_value=mock_response):
            result = self.fn("client_id", "client_secret", "old_refresh_token")

        self.assertIsInstance(result, dict)
        self.assertIn("access_token", result)
        self.assertEqual(result["access_token"], "new_access_token_xyz")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def json_bytes(obj: dict) -> bytes:
    import json
    return json.dumps(obj).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
