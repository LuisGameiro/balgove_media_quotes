"""Shared live feed discovery and article download.

Both ``mq fetch`` and the diagnostic script use this helper, so the T0 gate, host allowlist,
blob caching and client reuse are implemented once. Nothing here parses an article body —
callers decide what to do with the downloaded bytes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx

from .adapters import BlobCache, parse_rss_feed, parse_sitemap_xml
from .fetch import fetch_bytes

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
    cache: BlobCache | None = None,
) -> tuple[list[dict[str, str]], list[FetchedArticle]]:
    """Fetch a feed and the first N articles; return discovered entries and downloaded bytes."""
    cache = cache or BlobCache()

    async def run() -> tuple[list[dict[str, str]], list[FetchedArticle]]:
        # One client for the feed and every article, so the policy's connection limit applies.
        async with httpx.AsyncClient(**policy.client_kwargs()) as client:
            feed_bytes, _ = await fetch_bytes(
                feed_url, row=row, policy=policy, track_a_approved=row.usable, client=client
            )
            feed_text = feed_bytes.decode("utf-8", errors="replace")
            if "<urlset" in feed_text or "<sitemapindex" in feed_text:
                items = parse_sitemap_xml(
                    feed_text, topic_pack=topic_pack, require_topic=require_topic
                )
            else:
                items = parse_rss_feed(
                    feed_text, topic_pack=topic_pack, require_topic=require_topic
                )

            results: list[FetchedArticle] = []
            for item in items[: max(1, limit)]:
                url = item["url"]
                try:
                    raw, digest = await fetch_bytes(
                        url, row=row, policy=policy, track_a_approved=row.usable, client=client
                    )
                    _, blob_path = cache.store(row.source_id, raw)
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
