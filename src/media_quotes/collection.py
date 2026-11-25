"""Shared rights-gated article acquisition for CLI, diagnostics and proof reports."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from email.utils import parsedate_to_datetime

from .adapters import BlobCache
from .bing_news import discover_bing_news
from .fetch import FetchPolicy
from .live import (
    FetchedArticle,
    assert_collection_permitted,
    fetch_article_documents,
    fetch_feed_documents,
)
from .registry import SourceRegistryRow
from .topics import TopicPack


@dataclass(slots=True)
class CollectionResult:
    """Discovered entries, downloaded articles and collection-level errors."""

    mode: str
    discovered: list[dict[str, str]] = field(default_factory=list)
    articles: list[FetchedArticle] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)


def collect_feed(
    row: SourceRegistryRow,
    feed_url: str,
    *,
    policy: FetchPolicy,
    topic_pack: TopicPack | None,
    require_topic: bool,
    limit: int,
    start: date | None = None,
    end: date | None = None,
    extra_hosts: frozenset[str] = frozenset(),
    cache: BlobCache | None = None,
) -> CollectionResult:
    """Collect articles from one approved feed or sitemap."""
    assert_collection_permitted(row)
    discovered, articles = fetch_feed_documents(
        row,
        feed_url,
        policy=policy,
        topic_pack=topic_pack,
        require_topic=require_topic,
        limit=limit,
        start=start,
        end=end,
        extra_hosts=extra_hosts,
        cache=cache,
    )
    return CollectionResult(mode="feed", discovered=discovered, articles=articles)


def _normalise_items(items: Sequence[dict[str, str] | str]) -> list[dict[str, str]]:
    normalised: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, str):
            normalised.append({"url": item, "title": "", "published": ""})
        else:
            normalised.append(
                {
                    "url": item["url"],
                    "title": item.get("title", ""),
                    "published": item.get("publication_date") or item.get("published", ""),
                }
            )
    return normalised


def collect_urls(
    row: SourceRegistryRow,
    items: Sequence[dict[str, str] | str],
    *,
    policy: FetchPolicy,
    extra_hosts: frozenset[str] = frozenset(),
    cache: BlobCache | None = None,
) -> CollectionResult:
    """Collect explicit original-publisher article URLs."""
    assert_collection_permitted(row)
    normalised = _normalise_items(items)
    articles = fetch_article_documents(
        row,
        normalised,
        policy=policy,
        extra_hosts=extra_hosts,
        cache=cache,
    )
    return CollectionResult(mode="urls", discovered=normalised, articles=articles)


def _published_in_window(item: dict[str, str], start: date | None, end: date | None) -> bool:
    """Known dates must fit; unknown dates survive for article metadata verification."""
    if start is None and end is None:
        return True
    raw = str(item.get("published") or item.get("publication_date") or "").strip()
    if not raw:
        return True
    try:
        published = date.fromisoformat(raw[:10])
    except ValueError:
        try:  # Bing News emits RFC-2822 pubDate values, not ISO dates.
            published = parsedate_to_datetime(raw).date()
        except (TypeError, ValueError):
            return True
    return (start is None or published >= start) and (end is None or published <= end)


def collect_bing(
    row: SourceRegistryRow,
    domain: str,
    *,
    queries: tuple[str, ...],
    limit: int,
    policy: FetchPolicy,
    extra_hosts: frozenset[str] = frozenset(),
    cache: BlobCache | None = None,
    start: date | None = None,
    end: date | None = None,
) -> CollectionResult:
    """Discover original URLs through Bing News and collect them through the normal gate."""
    assert_collection_permitted(row)
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    errors: list[dict[str, str]] = []
    for query in queries:
        try:
            discovered = asyncio.run(discover_bing_news(domain, query=query, limit=limit))
        except Exception as exc:
            errors.append({"stage": "discovery", "error": str(exc) or type(exc).__name__})
            continue
        for item in discovered:
            if not _published_in_window(item, start, end):
                continue
            url = item.get("url", "")
            if url and url not in seen:
                seen.add(url)
                candidates.append(item)
        if len(candidates) >= limit * 2:
            break
    selected = candidates[: limit * 2]
    result = collect_urls(
        row,
        selected,
        policy=policy,
        extra_hosts=extra_hosts,
        cache=cache,
    )
    result.mode = "bing_discovery"
    result.errors.extend(errors)
    return result
