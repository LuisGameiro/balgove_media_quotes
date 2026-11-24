"""Shared live feed discovery and article download.

Both ``mq fetch`` and the diagnostic script use this helper, so the T0 gate, host allowlist,
blob caching and client reuse are implemented once. Nothing here parses an article body —
callers decide what to do with the downloaded bytes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx
from defusedxml.ElementTree import ParseError

from .adapters import BlobCache, parse_rss_feed, parse_sitemap_xml
from .exceptions import SourceNotPermitted
from .fetch import TokenBucket, fetch_bytes

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


def fetch_feed_documents(
    row: SourceRegistryRow,
    feed_url: str,
    *,
    policy: FetchPolicy,
    topic_pack: TopicPack | None,
    require_topic: bool,
    limit: int,
    extra_hosts: frozenset[str] = frozenset(),
    cache: BlobCache | None = None,
) -> tuple[list[dict[str, str]], list[FetchedArticle]]:
    """Fetch a feed and the first N articles; return discovered entries and downloaded bytes."""
    if not row.usable:
        raise SourceNotPermitted(f"source '{row.source_id}' is not permitted for live fetch")
    if row.access_mode not in {"public_rss_permitted", "official_api", "licensed_database"}:
        raise SourceNotPermitted(
            f"source '{row.source_id}' access_mode {row.access_mode!r} "
            "does not permit feed discovery"
        )
    cache = cache or BlobCache()

    async def run() -> tuple[list[dict[str, str]], list[FetchedArticle]]:
        feed_host = urlparse(feed_url).hostname
        hosts = set(extra_hosts)
        if feed_host:
            hosts.add(feed_host.lower())
        effective_hosts = frozenset(hosts)

        # Share one bucket across the feed and every article so the rate limit applies.
        bucket = TokenBucket(policy.requests_per_second)
        # One client for the feed and every article, so the policy's connection limit applies.
        async with httpx.AsyncClient(**policy.client_kwargs()) as client:
            feed_bytes, _ = await fetch_bytes(
                feed_url,
                row=row,
                policy=policy,
                track_a_approved=row.usable,
                check_dns=True,
                bucket=bucket,
                allowed_hosts=effective_hosts,
                client=client,
            )
            feed_text = feed_bytes.decode("utf-8", errors="replace")
            try:
                if "<urlset" in feed_text or "<sitemapindex" in feed_text:
                    items = parse_sitemap_xml(
                        feed_text, topic_pack=topic_pack, require_topic=require_topic
                    )
                else:
                    items = parse_rss_feed(
                        feed_text, topic_pack=topic_pack, require_topic=require_topic
                    )
            except ParseError:
                items = []

            results: list[FetchedArticle] = []
            for item in items[: max(1, limit)]:
                url = item["url"]
                try:
                    raw, digest = await fetch_bytes(
                        url,
                        row=row,
                        policy=policy,
                        track_a_approved=row.usable,
                        check_dns=True,
                        bucket=bucket,
                        allowed_hosts=effective_hosts,
                        client=client,
                    )
                    stored_digest, blob_path = cache.store(row.source_id, raw)
                    cache.store_meta(
                        row.source_id,
                        stored_digest,
                        {
                            "url": url,
                            "title": item.get("title", ""),
                            "published": item.get("published", ""),
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
            return items, results

    return asyncio.run(run())
