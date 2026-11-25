"""Bounded historical discovery of article URLs into the local blob cache.

This is the archive stage for ``mq backfill --fetch-archive``. It walks sitemap indexes up to a
hard limit, parses dated RSS/sitemap entries in ``[start, end]``, and stores article bytes through
the same rights-gated fetch policy used by ``mq fetch --live``. It never writes quotes or digests.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import httpx
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from .adapters import BlobCache, parse_rss_feed, parse_sitemap_xml
from .exceptions import FeedParseError
from .fetch import TokenBucket, allowed_hosts_for, fetch_bytes
from .live import _download_items

if TYPE_CHECKING:
    from .fetch import FetchPolicy
    from .registry import SourceRegistryRow
    from .topics import TopicPack


@dataclass(slots=True)
class ArchiveResult:
    """Summary of one source's historical discovery run."""

    source_id: str
    feed_url: str
    sitemaps_fetched: int = 0
    discovered_urls: int = 0
    stored_articles: int = 0
    errors: list[str] = field(default_factory=list)


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _sitemap_children(raw: str) -> list[str]:
    try:
        root = fromstring(raw)
    except (ParseError, DefusedXmlException, ValueError) as exc:
        raise FeedParseError(f"invalid sitemap index: {exc}") from exc
    urls: list[str] = []
    for element in root.iter():
        if _local_tag(element.tag).lower() != "sitemap":
            continue
        for child in element:
            if _local_tag(child.tag).lower() == "loc" and (child.text or "").strip():
                urls.append((child.text or "").strip())
    return urls


def discover_archive_documents(
    row: SourceRegistryRow,
    feed_url: str,
    *,
    policy: FetchPolicy,
    topic_pack: TopicPack | None,
    start: date,
    end: date,
    max_sitemaps: int = 50,
    max_articles: int = 200,
    require_topic: bool = True,
    extra_hosts: frozenset[str] = frozenset(),
    cache: BlobCache | None = None,
) -> ArchiveResult:
    """Fetch dated archive entries between ``start`` and ``end`` into the blob cache."""
    if end < start:
        raise ValueError("end must not precede start")
    if max_sitemaps < 1 or max_articles < 1:
        raise ValueError("archive limits must be positive")
    from .live import assert_collection_permitted

    assert_collection_permitted(row)
    cache = cache or BlobCache()
    result = ArchiveResult(source_id=row.source_id, feed_url=feed_url)
    discovered: list[dict[str, str]] = []
    permitted_hosts = allowed_hosts_for(row) | extra_hosts

    async def run() -> None:
        bucket = TokenBucket(policy.requests_per_second)
        items: list[dict[str, str]] = []
        seen_urls: set[str] = set()
        queue = [feed_url]
        visited: set[str] = set()
        attempts = 0
        async with httpx.AsyncClient(**policy.client_kwargs()) as client:
            while queue and attempts < max_sitemaps:
                current = queue.pop(0)
                if current in visited:
                    continue
                visited.add(current)
                attempts += 1
                try:
                    raw, _ = await fetch_bytes(
                        current,
                        row=row,
                        policy=policy,
                        track_a_approved=True,
                        check_dns=True,
                        bucket=bucket,
                        allowed_hosts=permitted_hosts,
                        client=client,
                    )
                except Exception as exc:  # one bad sitemap must not abort the archive
                    result.errors.append(f"{current}: {exc}")
                    continue
                result.sitemaps_fetched += 1
                text = raw.decode("utf-8", errors="replace")
                if "<sitemapindex" in text:
                    try:
                        children = _sitemap_children(text)
                    except FeedParseError as exc:
                        result.errors.append(str(exc))
                        continue
                    for child in children:
                        child_url = urljoin(current, child)
                        if child_url in visited:
                            continue
                        queue.append(child_url)
                    continue
                try:
                    if "<urlset" in text:
                        entries = parse_sitemap_xml(
                            text,
                            start=start,
                            end=end,
                            topic_pack=topic_pack,
                            require_topic=require_topic,
                        )
                    else:
                        entries = parse_rss_feed(
                            text,
                            start=start,
                            end=end,
                            topic_pack=topic_pack,
                            require_topic=require_topic,
                        )
                except (FeedParseError, ParseError, DefusedXmlException, ValueError) as exc:
                    result.errors.append(f"{current}: {exc}")
                    continue
                for entry in entries:
                    url = urljoin(current, entry.get("url", ""))
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    item = dict(entry)
                    item["url"] = url
                    items.append(item)
                    discovered.append(item)
                    if len(items) >= max_articles:
                        break
                if len(items) >= max_articles:
                    break
            if not items:
                return
            stored = await _download_items(
                client,
                row,
                items,
                policy=policy,
                bucket=bucket,
                allowed_hosts=permitted_hosts,
                cache=cache,
                feed_url=feed_url,
            )
            result.stored_articles = sum(1 for article in stored if not article.error)
            result.errors.extend(
                f"{article.url}: {article.error}" for article in stored if article.error
            )

    asyncio.run(run())
    result.discovered_urls = len(discovered)
    return result


__all__ = ["ArchiveResult", "discover_archive_documents"]
