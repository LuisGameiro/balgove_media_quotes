"""Shared live feed discovery and article download.

Both ``mq fetch`` and the diagnostic script use this helper, so the T0 gate, host allowlist,
blob caching and client reuse are implemented once. Nothing here parses an article body —
callers decide what to do with the downloaded bytes.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

import httpx
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError

from .adapters import BlobCache, parse_rss_feed, parse_sitemap_xml
from .bing_news import discover_bing_news
from .enums import AcquisitionProvenance
from .exceptions import SourceNotPermitted
from .fetch import TokenBucket, allowed_hosts_for, fetch_bytes

if TYPE_CHECKING:
    from .fetch import FetchPolicy
    from .registry import SourceRegistryRow
    from .source_configs import SourceConfig
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
    provenance: AcquisitionProvenance = "live_feed"
    discovered_via: str = "official_feed"


@dataclass(frozen=True, slots=True)
class LiveSourceResult:
    """Outcome of one source's live collection, including its discovery route and errors."""

    articles: list[FetchedArticle]
    discovered: list[dict[str, str]]
    provenance: AcquisitionProvenance
    route: str
    errors: list[str]


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
    transport: httpx.AsyncBaseTransport | None = None,
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
        client_kwargs = policy.client_kwargs()
        if transport is not None:
            client_kwargs["transport"] = transport
        async with httpx.AsyncClient(**client_kwargs) as client:
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
                        feed_text, topic_pack=topic_pack, require_topic=require_topic
                    )
                else:
                    items = parse_rss_feed(
                        feed_text, topic_pack=topic_pack, require_topic=require_topic
                    )
            except (ParseError, DefusedXmlException):
                items = []

            results: list[FetchedArticle] = []
            target_items = items[:limit] if limit > 0 else []
            for item in target_items:
                url = urljoin(feed_url, item["url"])
                try:
                    raw, digest = await fetch_bytes(
                        url,
                        row=row,
                        policy=policy,
                        track_a_approved=track_a_approved,
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
                            "published": item.get("publication_date") or item.get("published", ""),
                            "feed_url": feed_url,
                            "source_id": row.source_id,
                            "provenance": "live_feed",
                            "discovered_via": "official_feed",
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


def fetch_url_documents(
    row: SourceRegistryRow,
    items: list[dict[str, str]],
    *,
    policy: FetchPolicy,
    cache: BlobCache | None = None,
    extra_hosts: frozenset[str] = frozenset(),
    transport: httpx.AsyncBaseTransport | None = None,
    provenance: AcquisitionProvenance = "live_bing",
    discovered_via: str = "bing_news",
) -> list[FetchedArticle]:
    """Fetch original publisher URLs through the rights gate, one error per article.

    ``provenance``/``discovered_via`` label the acquisition path; archive discovery stamps
    them as ``live_feed``/``archive`` while Bing fallback keeps the defaults.
    """
    if not row.usable:
        raise SourceNotPermitted(f"source '{row.source_id}' is not permitted for live fetch")
    cache = cache or BlobCache()

    async def run() -> list[FetchedArticle]:
        permitted_hosts = allowed_hosts_for(row) | extra_hosts
        track_a_approved = bool(row.usable and row.license_reference)
        bucket = TokenBucket(policy.requests_per_second)
        client_kwargs = policy.client_kwargs()
        if transport is not None:
            client_kwargs["transport"] = transport
        results: list[FetchedArticle] = []
        async with httpx.AsyncClient(**client_kwargs) as client:
            for item in items:
                url = item.get("url", "")
                try:
                    raw, digest = await fetch_bytes(
                        url,
                        row=row,
                        policy=policy,
                        track_a_approved=track_a_approved,
                        check_dns=True,
                        bucket=bucket,
                        allowed_hosts=permitted_hosts,
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
                            "source_id": row.source_id,
                            "provenance": provenance,
                            "discovered_via": discovered_via,
                        },
                    )
                    results.append(
                        FetchedArticle(
                            url=url,
                            title=item.get("title", ""),
                            raw=raw,
                            sha256=digest,
                            blob_path=str(blob_path),
                            provenance=provenance,
                            discovered_via=discovered_via,
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
                            provenance=provenance,
                            discovered_via=discovered_via,
                        )
                    )
        return results

    return asyncio.run(run())


def collect_live_source(
    row: SourceRegistryRow,
    *,
    feed_url: str,
    policy: FetchPolicy,
    limit: int,
    topic_pack: TopicPack | None = None,
    cache: BlobCache | None = None,
    source_config: SourceConfig | None = None,
    extra_hosts: frozenset[str] = frozenset(),
    transport: httpx.AsyncBaseTransport | None = None,
) -> LiveSourceResult:
    """Collect from the official feed, falling back to opt-in Bing discovery only on failure."""
    if not row.usable:
        raise SourceNotPermitted(f"source '{row.source_id}' is not permitted for live fetch")
    if row.access_mode not in {"public_rss_permitted", "official_api", "licensed_database"}:
        raise SourceNotPermitted(
            f"source '{row.source_id}' access_mode {row.access_mode!r} "
            "does not permit feed discovery"
        )
    hosts = extra_hosts
    if not hosts and source_config is not None:
        hosts = frozenset([*source_config.content_hosts, *source_config.redirect_hosts])

    errors: list[str] = []
    try:
        discovered, articles = fetch_feed_documents(
            row,
            feed_url,
            policy=policy,
            topic_pack=topic_pack,
            require_topic=True,
            limit=limit,
            extra_hosts=hosts,
            cache=cache,
            transport=transport,
        )
        errors.extend(article.error for article in articles if article.error)
    except Exception as exc:  # a failed feed still qualifies for the configured fallback
        discovered, articles, errors = [], [], [str(exc)]

    official_feed = LiveSourceResult(
        articles=articles,
        discovered=discovered,
        provenance="live_feed",
        route="official_feed",
        errors=errors,
    )
    if any(not article.error for article in articles):
        return official_feed
    if source_config is None or not source_config.bing_discovery_enabled:
        return official_feed

    domain = (urlparse(row.homepage_url).hostname or "").lower()
    bing_errors: list[str] = []
    if not domain:
        bing_errors.append(f"source '{row.source_id}' has no homepage domain for Bing discovery")
        candidates: list[dict[str, str]] = []
    else:
        try:
            candidates = asyncio.run(discover_bing_news(domain, limit=limit, transport=transport))
        except Exception as exc:
            bing_errors.append(f"Bing discovery failed: {exc}")
            candidates = []

    # Never auto-add a host learned from Bing: only explicitly configured hosts are eligible.
    allowed = hosts | allowed_hosts_for(row)
    kept: list[dict[str, str]] = []
    for candidate in candidates:
        host = (urlparse(candidate.get("url", "")).hostname or "").lower()
        if host not in allowed:
            bing_errors.append(
                f"bing candidate host {host!r} is not configured for source '{row.source_id}'"
            )
            continue
        kept.append(candidate)

    if not kept:
        return LiveSourceResult(
            articles=articles,
            discovered=discovered,
            provenance="live_feed",
            route="official_feed",
            errors=errors + bing_errors,
        )

    bing_articles = fetch_url_documents(
        row,
        kept,
        policy=policy,
        cache=cache,
        extra_hosts=hosts,
        transport=transport,
    )
    errors.extend(bing_errors)
    errors.extend(article.error for article in bing_articles if article.error)
    return LiveSourceResult(
        articles=bing_articles,
        discovered=kept,
        provenance="live_bing",
        route="bing_news",
        errors=errors,
    )
