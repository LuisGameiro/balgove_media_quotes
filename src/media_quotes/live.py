"""Shared live feed discovery and article download.

Both ``mq fetch`` and the diagnostic script use this helper, so the T0 gate, host allowlist,
blob caching and client reuse are implemented once. Nothing here parses an article body —
callers decide what to do with the downloaded bytes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import httpx
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError

from .adapters import BlobCache, parse_rss_feed, parse_sitemap_xml
from .exceptions import FeedParseError, SourceBlockedError, SourceNotPermitted
from .fetch import TokenBucket, allowed_hosts_for, fetch_bytes

if TYPE_CHECKING:
    from .fetch import FetchPolicy
    from .registry import SourceRegistryRow
    from .topics import TopicPack


@dataclass(frozen=True, slots=True)
class FetchedArticle:
    """One downloaded article, or the error that prevented it."""

    url: str
    title: str
    raw: bytes
    sha256: str
    blob_path: str
    error: str = ""


def assert_collection_permitted(row: SourceRegistryRow) -> None:
    """Require both rights clearance and operational availability for live collection."""
    if not row.usable:
        raise SourceNotPermitted(f"source '{row.source_id}' is not permitted for live fetch")
    if row.tier_block:
        raise SourceBlockedError(row.blocker_reason or f"source '{row.source_id}' is tier_blocked")


async def _download_items(
    client: httpx.AsyncClient,
    row: SourceRegistryRow,
    items: list[dict[str, str]],
    *,
    policy: FetchPolicy,
    bucket: TokenBucket,
    allowed_hosts: frozenset[str],
    cache: BlobCache,
    feed_url: str = "",
) -> list[FetchedArticle]:
    results: list[FetchedArticle] = []
    for item in items:
        url = urljoin(feed_url or row.homepage_url, item["url"])
        try:
            raw, digest = await fetch_bytes(
                url,
                row=row,
                policy=policy,
                track_a_approved=True,
                check_dns=True,
                bucket=bucket,
                allowed_hosts=allowed_hosts,
                client=client,
            )
            stored_digest, blob_path = cache.store(row.source_id, raw)
            cache.store_meta(
                row.source_id,
                stored_digest,
                {
                    "url": url,
                    "title": item.get("title", ""),
                    "published": item.get("publication_date") or item.get("published", ""),
                    "feed_url": feed_url,
                    "source_id": row.source_id,
                },
            )
            results.append(
                FetchedArticle(
                    url=url,
                    title=item.get("title", ""),
                    raw=raw,
                    sha256=digest,
                    blob_path=str(blob_path),
                )
            )
        except Exception as exc:  # one bad article must not abort the rest
            results.append(
                FetchedArticle(
                    url=url,
                    title=item.get("title", ""),
                    raw=b"",
                    sha256="",
                    blob_path="",
                    error=str(exc),
                )
            )
    return results


def fetch_feed_documents(
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
) -> tuple[list[dict[str, str]], list[FetchedArticle]]:
    """Fetch a feed and the first N articles; return discovered entries and downloaded bytes."""
    assert_collection_permitted(row)
    if row.access_mode not in {"public_rss_permitted", "official_api", "licensed_database"}:
        raise SourceNotPermitted(
            f"source '{row.source_id}' access_mode {row.access_mode!r} "
            "does not permit feed discovery"
        )
    cache = cache or BlobCache()

    async def run() -> tuple[list[dict[str, str]], list[FetchedArticle]]:
        permitted_hosts = allowed_hosts_for(row) | extra_hosts
        feed_host = (urlparse(feed_url).hostname or "").lower()
        if not feed_host or feed_host not in permitted_hosts:
            raise ValueError(
                f"feed URL host {feed_host!r} is not registered for source {row.source_id!r}"
            )
        effective_hosts = permitted_hosts
        track_a_approved = bool(row.usable and row.license_reference)

        # Share one bucket across the feed and every article so the rate limit applies.
        bucket = TokenBucket(policy.requests_per_second)
        # One client for the feed and every article, so the policy's connection limit applies.
        async with httpx.AsyncClient(**policy.client_kwargs()) as client:
            feed_bytes, _ = await fetch_bytes(
                feed_url,
                row=row,
                policy=policy,
                track_a_approved=track_a_approved,
                check_dns=True,
                bucket=bucket,
                allowed_hosts=effective_hosts,
                client=client,
            )
            feed_text = feed_bytes.decode("utf-8", errors="replace")
            try:
                if "<urlset" in feed_text or "<sitemapindex" in feed_text:
                    items = parse_sitemap_xml(
                        feed_text,
                        start=start,
                        end=end,
                        topic_pack=topic_pack,
                        require_topic=require_topic,
                    )
                else:
                    items = parse_rss_feed(
                        feed_text,
                        start=start,
                        end=end,
                        topic_pack=topic_pack,
                        require_topic=require_topic,
                    )
            except (ParseError, DefusedXmlException, ValueError) as exc:
                raise FeedParseError(f"invalid discovery feed {feed_url}: {exc}") from exc

            target_items = items[:limit] if limit > 0 else []
            results = await _download_items(
                client,
                row,
                target_items,
                policy=policy,
                bucket=bucket,
                allowed_hosts=effective_hosts,
                cache=cache,
                feed_url=feed_url,
            )
            return items, results

    return asyncio.run(run())


def fetch_article_documents(
    row: SourceRegistryRow,
    items: list[dict[str, str]],
    *,
    policy: FetchPolicy,
    extra_hosts: frozenset[str] = frozenset(),
    cache: BlobCache | None = None,
) -> list[FetchedArticle]:
    """Fetch explicit article URLs through the same rights, rate and cache policy as feeds."""
    assert_collection_permitted(row)
    cache = cache or BlobCache()
    permitted_hosts = allowed_hosts_for(row) | extra_hosts

    async def run() -> list[FetchedArticle]:
        bucket = TokenBucket(policy.requests_per_second)
        async with httpx.AsyncClient(**policy.client_kwargs()) as client:
            return await _download_items(
                client,
                row,
                items,
                policy=policy,
                bucket=bucket,
                allowed_hosts=permitted_hosts,
                cache=cache,
            )

    return asyncio.run(run())
