"""Tests for free Bing News RSS discovery."""

from __future__ import annotations

import asyncio
from urllib.parse import quote

import httpx

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
