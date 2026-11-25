"""Tests for free Bing News RSS discovery."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote

import httpx
import pytest

from media_quotes.bing_news import discover_bing_news


def _feed(urls: list[str]) -> bytes:
    items = []
    for index, url in enumerate(urls):
        redirect = "https://www.bing.com/news/apiclick.aspx?url=" + quote(url, safe="")
        items.append(
            f"<item><title>Story {index}</title><link>{redirect}</link>"
            f"<pubDate>Tue, 24 Nov 2026 10:00:00 GMT</pubDate></item>"
        )
    return f"<rss><channel>{''.join(items)}</channel></rss>".encode()


def test_discover_bing_news_decodes_original_urls_and_filters_domain() -> None:
    requested: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url)
        return httpx.Response(
            200,
            content=_feed(
                [
                    "https://www.reuters.com/world/article-one",
                    "https://www.reuters.com/world/article-two",
                    "https://example.com/not-reuters",
                ]
            ),
        )

    results = asyncio.run(
        discover_bing_news(
            "reuters.com",
            query="Ukraine",
            limit=2,
            transport=httpx.MockTransport(handler),
        )
    )

    assert [item["url"] for item in results] == [
        "https://www.reuters.com/world/article-one",
        "https://www.reuters.com/world/article-two",
    ]
    assert requested[0].params["q"] == "site:reuters.com Ukraine"
    assert requested[0].params["format"] == "rss"


def _run(handler: Any, **kwargs: Any) -> list[dict[str, str]]:
    transport = httpx.MockTransport(handler)
    return asyncio.run(discover_bing_news("reuters.com", transport=transport, **kwargs))


def test_discover_bing_news_validates_limit_and_domain() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be made for invalid inputs")

    with pytest.raises(ValueError, match="limit must be positive"):
        _run(handler, limit=0)
    with pytest.raises(ValueError, match="domain must not be empty"):
        asyncio.run(discover_bing_news("   ", transport=httpx.MockTransport(handler)))


def test_discover_bing_news_raises_for_http_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(httpx.HTTPStatusError):
        _run(handler)


def test_discover_bing_news_rejects_oversized_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5_000_001)

    with pytest.raises(ValueError, match="size limit"):
        _run(handler)


def test_discover_bing_news_rejects_invalid_xml() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<rss><unclosed>")

    with pytest.raises(ValueError, match="invalid XML"):
        _run(handler)


def test_discover_bing_news_returns_empty_for_empty_feed() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_feed([]))

    assert _run(handler) == []


def test_discover_bing_news_filters_scheme_host_and_duplicates() -> None:
    content = b"""<rss><channel>
      <item><title>A</title><link>https://www.reuters.com/a</link></item>
      <item><title>dup</title><link>https://www.reuters.com/a</link></item>
      <item><title>insecure</title><link>http://www.reuters.com/b</link></item>
      <item><title>other</title><link>https://example.com/c</link></item>
      <item><title>subdomain</title><link>https://news.reuters.com/d</link></item>
    </channel></rss>"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    results = _run(handler)
    assert [item["url"] for item in results] == [
        "https://www.reuters.com/a",
        "https://news.reuters.com/d",
    ]
    assert results[0]["title"] == "A"


def test_discover_bing_news_reads_atom_link_href_and_published() -> None:
    content = (
        b'<feed><entry><title>Atom</title><link href="https://www.reuters.com/atom"/>'
        b"<published>2026-11-24</published></entry></feed>"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    results = _run(handler)
    assert results == [
        {
            "title": "Atom",
            "url": "https://www.reuters.com/atom",
            "published": "2026-11-24",
        }
    ]
