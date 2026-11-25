"""Tests for the shared article collection service."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from media_quotes import collection
from media_quotes.exceptions import SourceBlockedError
from media_quotes.fetch import FetchPolicy
from media_quotes.live import FetchedArticle
from media_quotes.registry import load_sources_csv

CSV = Path(__file__).resolve().parents[2] / "data/sources.csv"
ROW = next(row for row in load_sources_csv(CSV) if row.source_id == "guardian")


def test_collect_feed_passes_dates_and_redirect_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_feed(row: Any, feed_url: str, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return [{"url": "https://article", "title": "Story"}], []

    monkeypatch.setattr(collection, "fetch_feed_documents", fake_feed)

    result = collection.collect_feed(
        ROW,
        "https://feed",
        policy=FetchPolicy(),
        topic_pack=None,
        require_topic=True,
        limit=3,
        start=date(2026, 11, 1),
        end=date(2026, 11, 24),
        extra_hosts=frozenset({"partner.example"}),
    )

    assert result.mode == "feed"
    assert seen["start"] == date(2026, 11, 1)
    assert seen["end"] == date(2026, 11, 24)
    assert seen["extra_hosts"] == frozenset({"partner.example"})


def test_collect_urls_normalises_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, str]] = []

    def fake_articles(row: Any, items: list[dict[str, str]], **kwargs: Any) -> Any:
        seen.extend(items)
        return [
            FetchedArticle(
                url=items[0]["url"],
                title=items[0]["title"],
                raw=b"article",
                sha256="a" * 64,
                blob_path="blob.html",
            )
        ]

    monkeypatch.setattr(collection, "fetch_article_documents", fake_articles)

    result = collection.collect_urls(
        ROW,
        [
            "https://article",
            {"url": "https://second", "title": "Second", "published": "2026-11-19"},
        ],
        policy=FetchPolicy(),
    )

    assert seen[0]["url"] == "https://article"
    assert seen[1]["title"] == "Second"
    assert seen[1]["published"] == "2026-11-19"
    assert result.articles[0].raw == b"article"


def test_collect_bing_deduplicates_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_discover(domain: str, *, query: str, limit: int) -> list[dict[str, str]]:
        return [
            {"url": "https://reuters.com/one", "title": "One"},
            {"url": "https://reuters.com/one", "title": "Duplicate"},
        ]

    seen: list[dict[str, str]] = []

    def fake_urls(row: Any, items: list[dict[str, str]], **kwargs: Any) -> Any:
        seen.extend(items)
        return collection.CollectionResult(mode="urls", discovered=items, articles=[])

    monkeypatch.setattr(collection, "discover_bing_news", fake_discover)
    monkeypatch.setattr(collection, "collect_urls", fake_urls)
    row = ROW.model_copy(
        update={
            "homepage_url": "https://www.reuters.com",
            "tier_block": False,
            "blocker_reason": "",
        }
    )

    result = collection.collect_bing(
        row,
        "reuters.com",
        queries=("Ukraine",),
        limit=2,
        policy=FetchPolicy(),
    )

    assert result.mode == "bing_discovery"
    assert len(seen) == 1
    assert seen[0]["url"] == "https://reuters.com/one"


def test_collect_bing_drops_known_out_of_window_dates_but_keeps_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_discover(domain: str, *, query: str, limit: int) -> list[dict[str, str]]:
        return [
            {"url": "https://article/old", "published": "2026-10-01"},
            {
                "url": "https://article/rfc-old",
                "published": "Tue, 01 Nov 2026 10:00:00 GMT",
            },
            {"url": "https://article/unknown", "published": ""},
            {"url": "https://article/current", "publication_date": "2026-11-19"},
        ]

    seen: list[dict[str, str]] = []

    def fake_urls(row: Any, items: list[dict[str, str]], **kwargs: Any) -> Any:
        seen.extend(items)
        return collection.CollectionResult(mode="urls", discovered=items, articles=[])

    monkeypatch.setattr(collection, "discover_bing_news", fake_discover)
    monkeypatch.setattr(collection, "collect_urls", fake_urls)
    row = ROW.model_copy(
        update={
            "homepage_url": "https://www.theguardian.com",
            "tier_block": False,
            "blocker_reason": "",
        }
    )

    collection.collect_bing(
        row,
        "theguardian.com",
        queries=("Ukraine",),
        limit=5,
        policy=FetchPolicy(),
        start=date(2026, 11, 7),
        end=date(2026, 11, 20),
    )

    assert [item["url"] for item in seen] == [
        "https://article/unknown",
        "https://article/current",
    ]


def test_published_in_window_parses_rfc822_and_keeps_unparseable_dates() -> None:
    start, end = date(2026, 11, 7), date(2026, 11, 20)

    assert not collection._published_in_window(
        {"published": "Thu, 01 Oct 2026 09:00:00 GMT"}, start, end
    )
    assert collection._published_in_window(
        {"published": "Thu, 19 Nov 2026 09:00:00 GMT"}, start, end
    )
    assert not collection._published_in_window({"published": "2026-10-01"}, start, end)
    assert collection._published_in_window({"published": "not a date"}, start, end)
    assert collection._published_in_window({"published": ""}, start, end)


def test_blocked_source_is_refused_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_fetch(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network collection must not run")

    monkeypatch.setattr(collection, "fetch_feed_documents", no_fetch)
    blocked = ROW.model_copy(
        update={"tier_block": True, "blocker_reason": "No quote-bearing content"}
    )

    with pytest.raises(SourceBlockedError, match="No quote-bearing content"):
        collection.collect_feed(
            blocked,
            "https://feed",
            policy=FetchPolicy(),
            topic_pack=None,
            require_topic=True,
            limit=1,
        )
