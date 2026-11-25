"""Pass 3 — historical archive discovery through approved, rights-gated source routes.

Discovery is metadata-only: this module never downloads article bodies, never reads fixtures,
and never falls back to Bing News or a source's current feed. Every fetched URL passes the
same HTTPS host allowlist used by live collection.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import httpx
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from .adapters import parse_rss_feed, parse_sitemap_xml
from .exceptions import SourceNotPermitted
from .fetch import TokenBucket, allowed_hosts_for, assert_url_permitted, fetch_bytes

if TYPE_CHECKING:
    from .fetch import FetchPolicy
    from .registry import SourceRegistryRow
    from .source_configs import SourceConfig, SourceRouteConfig

_PARSE_ERRORS = (ParseError, DefusedXmlException, ValueError)


@dataclass(frozen=True, slots=True)
class HistoricalArticle:
    """One historical article URL discovered from an approved archive route."""

    source_id: str
    url: str
    title: str
    published: str
    discovery_url: str


@dataclass(frozen=True, slots=True)
class ArchiveDiscoveryResult:
    """Discovered candidates, per-route errors, and the number of sitemap documents read."""

    candidates: list[HistoricalArticle]
    errors: list[str]
    sitemaps_fetched: int


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _route_supported(route: SourceRouteConfig) -> bool:
    """Only feed/sitemap routes are machine-readable archive discovery routes."""
    return any(marker in route.route_type.lower() for marker in ("rss", "sitemap", "feed", "atom"))


def _payload_kind(text: str) -> str:
    lowered = text.lower()
    if "<sitemapindex" in lowered:
        return "sitemapindex"
    if "<urlset" in lowered:
        return "urlset"
    if "<rss" in lowered or "<feed" in lowered:
        return "rss"
    return "unsupported"


def _sitemap_index_locs(raw: bytes) -> list[str]:
    """Extract child <loc> values from <sitemap> entries of a sitemap index."""
    locs: list[str] = []
    for elem in fromstring(raw).iter():
        if _local_tag(str(elem.tag)) != "sitemap":
            continue
        for child in elem:
            if _local_tag(str(child.tag)) == "loc" and (child.text or "").strip():
                locs.append((child.text or "").strip())
                break
    return locs


def discover_historical_articles(
    row: SourceRegistryRow,
    config: SourceConfig,
    *,
    start: date,
    end: date,
    topic_id: str,
    policy: FetchPolicy,
    limit: int = 1000,
    max_sitemaps: int = 25,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ArchiveDiscoveryResult:
    """Discover historical article URLs from approved RSS/sitemap routes for ``topic_id``."""
    if not row.usable:
        raise SourceNotPermitted(f"source '{row.source_id}' is not permitted for archive discovery")
    allowed_hosts = (
        allowed_hosts_for(row) | frozenset(config.content_hosts) | frozenset(config.redirect_hosts)
    )
    track_a_approved = bool(row.usable and row.license_reference)
    routes = sorted(
        (r for r in config.routes if r.approved and (not r.topic_id or r.topic_id == topic_id)),
        key=lambda r: (r.priority, r.route_id),
    )
    errors: list[str] = []
    candidates: list[HistoricalArticle] = []
    seen: set[str] = set()
    sitemaps_fetched = 0

    def add_entries(entries: list[dict[str, str]], discovery_url: str) -> None:
        for entry in entries:
            url = urljoin(discovery_url, entry.get("url", ""))
            if not url or url in seen:
                continue
            published = entry.get("publication_date") or ""
            if published:
                try:
                    pub_date = date.fromisoformat(published)
                except ValueError:
                    pub_date = None  # undated candidates survive for post-fetch verification
                if pub_date and (pub_date < start or pub_date > end):
                    continue
            # shortcut: archive_article_limit caps candidates/source; raise it for larger archives.
            if limit > 0 and len(candidates) >= limit:
                return
            seen.add(url)
            candidates.append(
                HistoricalArticle(
                    source_id=row.source_id,
                    url=url,
                    title=entry.get("title", ""),
                    published=published,
                    discovery_url=discovery_url,
                )
            )

    async def run() -> None:
        nonlocal sitemaps_fetched
        bucket = TokenBucket(policy.requests_per_second)
        client_kwargs = policy.client_kwargs()
        if transport is not None:
            client_kwargs["transport"] = transport
        client = httpx.AsyncClient(**client_kwargs)

        def permitted(url: str, *, child: bool) -> bool:
            try:
                assert_url_permitted(url, row, extra_hosts=allowed_hosts, check_dns=True)
            except ValueError as exc:
                label = "child sitemap" if child else "discovery URL"
                errors.append(f"{label} {url!r} rejected: {exc}")
                return False
            return True

        async def fetch(url: str) -> bytes | None:
            try:
                raw, _ = await fetch_bytes(
                    url,
                    row=row,
                    policy=policy,
                    track_a_approved=track_a_approved,
                    bucket=bucket,
                    allowed_hosts=allowed_hosts,
                    client=client,
                    check_dns=True,
                )
            except Exception as exc:  # one bad document must not abort the rest
                errors.append(f"failed to fetch {url!r}: {exc}")
                return None
            return raw

        async def walk_sitemap(url: str) -> None:
            nonlocal sitemaps_fetched
            if limit > 0 and len(candidates) >= limit:
                return
            if not permitted(url, child=True):
                return
            if sitemaps_fetched >= max_sitemaps:
                errors.append(f"sitemap limit ({max_sitemaps}) reached; skipping {url!r}")
                return
            raw = await fetch(url)
            if raw is None:
                return
            sitemaps_fetched += 1
            if _payload_kind(raw.decode("utf-8", errors="replace")) == "sitemapindex":
                try:
                    for child in _sitemap_index_locs(raw):
                        await walk_sitemap(child)
                except _PARSE_ERRORS as exc:
                    errors.append(f"invalid sitemap index at {url!r}: {exc}")
            else:
                try:
                    add_entries(parse_sitemap_xml(raw, start=start, end=end), url)
                except _PARSE_ERRORS as exc:
                    errors.append(f"invalid sitemap at {url!r}: {exc}")

        async with client:
            for route in routes:
                if limit > 0 and len(candidates) >= limit:
                    break
                if not route.discovery_url:
                    errors.append(f"route {route.route_id!r} has no discovery_url")
                    continue
                if not _route_supported(route):
                    errors.append(
                        f"unsupported route type {route.route_type!r} for route {route.route_id!r}"
                    )
                    continue
                if not permitted(route.discovery_url, child=False):
                    continue
                raw = await fetch(route.discovery_url)
                if raw is None:
                    continue
                kind = _payload_kind(raw.decode("utf-8", errors="replace"))
                if kind in {"sitemapindex", "urlset"} and sitemaps_fetched >= max_sitemaps:
                    errors.append(
                        f"sitemap limit ({max_sitemaps}) reached; skipping {route.discovery_url!r}"
                    )
                    continue
                if kind == "sitemapindex":
                    sitemaps_fetched += 1
                    try:
                        for child in _sitemap_index_locs(raw):
                            await walk_sitemap(child)
                    except _PARSE_ERRORS as exc:
                        errors.append(f"invalid sitemap index at {route.discovery_url!r}: {exc}")
                elif kind == "urlset":
                    sitemaps_fetched += 1
                    try:
                        add_entries(
                            parse_sitemap_xml(raw, start=start, end=end), route.discovery_url
                        )
                    except _PARSE_ERRORS as exc:
                        errors.append(f"invalid sitemap at {route.discovery_url!r}: {exc}")
                elif kind == "rss":
                    try:
                        add_entries(parse_rss_feed(raw, start=start, end=end), route.discovery_url)
                    except _PARSE_ERRORS as exc:
                        errors.append(f"invalid feed at {route.discovery_url!r}: {exc}")
                else:
                    errors.append(
                        f"unsupported discovery payload at {route.discovery_url!r} "
                        f"for route {route.route_id!r}"
                    )

    asyncio.run(run())
    return ArchiveDiscoveryResult(
        candidates=candidates, errors=errors, sitemaps_fetched=sitemaps_fetched
    )
