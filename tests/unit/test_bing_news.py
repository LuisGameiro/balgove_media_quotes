"""Tests for free Bing News RSS discovery."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx
import pytest

from media_quotes.bing_news import discover_bing_news
from media_quotes.exceptions import RateLimitExceededError
from media_quotes.fetch import TokenBucket


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


def test_discover_bing_news_rejects_oversized_streamed_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5_000_001)

    with pytest.raises(ValueError, match="exceeds size limit"):
        asyncio.run(
            discover_bing_news(
                "reuters.com",
                transport=httpx.MockTransport(handler),
                clock=10.0,
            )
        )


def test_discover_bing_news_throttles_with_shared_bucket_without_sleeping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_feed([]))

    transport = httpx.MockTransport(handler)
    bucket = TokenBucket(rate_per_second=1.0, last_refill=10.0)

    assert (
        asyncio.run(
            discover_bing_news("reuters.com", transport=transport, bucket=bucket, clock=10.0)
        )
        == []
    )
    assert bucket.tokens == 0.0

    # The same bucket is now empty: a second discovery is refused before any request.
    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("transport must not be called while rate limited")

    with pytest.raises(RateLimitExceededError):
        asyncio.run(
            discover_bing_news(
                "reuters.com",
                transport=httpx.MockTransport(forbidden),
                bucket=bucket,
                clock=10.0,
            )
        )
