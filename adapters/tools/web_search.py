from __future__ import annotations

import html
import re
import urllib.parse
import urllib.request
from typing import Any


SEARCH_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = "Mozilla/5.0 (compatible; APEX/1.0; +https://duckduckgo.com/)"


def search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Run a DuckDuckGo HTML search and return structured results."""
    if not query or not query.strip():
        raise ValueError("query must be a non-empty string")
    if max_results <= 0:
        return []

    payload = urllib.parse.urlencode({"q": query}).encode("utf-8")
    request = urllib.request.Request(
        SEARCH_URL,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=20) as response:
        html_text = response.read().decode("utf-8", errors="replace")

    return _parse_results(html_text, max_results=max_results)


def _parse_results(html_text: str, max_results: int) -> list[dict[str, str]]:
    blocks = re.findall(
        r'(?s)<div class="result(?:\s+results_links[^"]*)?".*?</div>\s*</div>',
        html_text,
    )

    results: list[dict[str, str]] = []
    for block in blocks:
        anchor_match = re.search(
            r'(?s)<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            block,
        )
        snippet_match = re.search(
            r'(?s)<a[^>]*class="result__snippet"[^>]*>(.*?)</a>|<div[^>]*class="result__snippet"[^>]*>(.*?)</div>',
            block,
        )
        if not anchor_match:
            continue

        raw_url = html.unescape(anchor_match.group(1))
        title = _clean_html(anchor_match.group(2))
        snippet_raw = snippet_match.group(1) or snippet_match.group(2) if snippet_match else ""
        snippet = _clean_html(snippet_raw)
        url = _normalize_duckduckgo_url(raw_url)
        if not snippet:
            block_text = _clean_html(block)
            snippet = block_text.replace(title, "", 1).strip(" -:\n\t")
        if not snippet:
            snippet = title

        if not title or not url:
            continue

        results.append(
            {
                "title": title,
                "url": url,
                "snippet": snippet,
                "source": "duckduckgo",
            }
        )
        if len(results) >= max_results:
            break

    return results


def _normalize_duckduckgo_url(url: str) -> str:
    if url.startswith("//"):
        url = "https:" + url
    parsed = urllib.parse.urlparse(url)
    if "duckduckgo.com" in parsed.netloc and parsed.path == "/l/":
        params = urllib.parse.parse_qs(parsed.query)
        uddg = params.get("uddg", [""])[0]
        if uddg:
            return urllib.parse.unquote(uddg)
    return url


def _clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()
