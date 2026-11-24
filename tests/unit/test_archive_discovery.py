"""Pass 3: rights-gated historical archive discovery from approved source routes."""

from __future__ import annotations

import socket
from datetime import date
from pathlib import Path

import httpx
import pytest

from media_quotes.adapters import BlobCache
from media_quotes.archive import (
    ArchiveDiscoveryResult,
    HistoricalArticle,
    discover_historical_articles,
)
from media_quotes.exceptions import SourceNotPermitted
from media_quotes.fetch import FetchPolicy
from media_quotes.live import fetch_url_documents
from media_quotes.source_configs import SourceConfig, SourceRouteConfig

START = date(2026, 10, 1)
END = date(2026, 11, 30)
POLICY = FetchPolicy(requests_per_second=1000.0)
HOME = "https://www.example.com"


@pytest.fixture(autouse=True)
def _global_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """check_dns=True in fetch_url_documents must stay offline."""

    def fake_getaddrinfo(host: str, port: int, **kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _route(
    route_id: str,
    url: str,
    *,
    route_type: str = "rss",
    topic_id: str = "ukraine",
    approved: bool = True,
) -> SourceRouteConfig:
    return SourceRouteConfig(
        route_id=route_id,
        route_type=route_type,
        discovery_url=url,
        topic_rule_id=f"{topic_id}-v1",
        topic_id=topic_id,
        approved=approved,
        license_reference="LIC-ARCHIVE-1",
    )


def _config(routes: list[SourceRouteConfig]) -> SourceConfig:
    return SourceConfig(
        source_id="archive-news",
        source_name="Archive News",
        homepage_url=f"{HOME}/",
        feed_url="https://feeds.example.com/rss.xml",
        tier=2,
        cadence_hours=24,
        country_region="Global",
        source_type="independent",
        language="en",
        rights_status="license_held",
        access_mode="public_rss_permitted",
        license_reference="LIC-ARCHIVE-1",
        discovery_method="rss",
        archive_start_date=START,
        enabled=True,
        routes=routes,
    )


def _transport(
    pages: dict[str, bytes | str], requested: list[str] | None = None
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if requested is not None:
            requested.append(url)
        body = pages.get(url)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body.encode() if isinstance(body, str) else body)

    return httpx.MockTransport(handler)


def _discover(
    config: SourceConfig,
    transport: httpx.AsyncBaseTransport,
    *,
    limit: int = 1000,
    max_sitemaps: int = 25,
) -> ArchiveDiscoveryResult:
    return discover_historical_articles(
        config.to_registry_row(),
        config,
        start=START,
        end=END,
        topic_id="ukraine",
        policy=POLICY,
        limit=limit,
        max_sitemaps=max_sitemaps,
        transport=transport,
    )


def _rss(*entries: tuple[str, str, str]) -> str:
    items = []
    for url, title, published in entries:
        pub = f"<pubDate>{published}</pubDate>" if published else ""
        items.append(f"<item><title>{title}</title><link>{url}</link>{pub}</item>")
    return f"<rss><channel>{''.join(items)}</channel></rss>"


def _urlset(*entries: tuple[str, str]) -> str:
    urls = []
    for url, lastmod in entries:
        mod = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        urls.append(f"<url><loc>{url}</loc>{mod}</url>")
    return (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(urls) + "</urlset>"
    )


def _sitemapindex(*locs: str) -> str:
    entries = "".join(f"<sitemap><loc>{loc}</loc></sitemap>" for loc in locs)
    return (
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        + entries
        + "</sitemapindex>"
    )


def test_sitemapindex_recursion() -> None:
    index = f"{HOME}/sitemap.xml"
    child = f"{HOME}/sitemap-a.xml"
    nested_index = f"{HOME}/sitemap-index-2.xml"
    grandchild = f"{HOME}/sitemap-b.xml"
    pages = {
        index: _sitemapindex(child, nested_index),
        child: _urlset(("https://www.example.com/news/a", "2026-10-05")),
        nested_index: _sitemapindex(grandchild),
        grandchild: _urlset(("https://www.example.com/news/b", "2026-11-02")),
    }
    requested: list[str] = []
    config = _config([_route("sitemap-main", index, route_type="sitemap")])

    result = _discover(config, _transport(pages, requested))

    assert result.errors == []
    assert result.sitemaps_fetched == 4
    assert [c.url for c in result.candidates] == [
        "https://www.example.com/news/a",
        "https://www.example.com/news/b",
    ]
    assert [c.discovery_url for c in result.candidates] == [child, grandchild]
    assert set(requested) == {index, child, nested_index, grandchild}


def test_date_filtering_keeps_in_range_and_undated_candidates() -> None:
    feed = "https://feeds.example.com/archive.xml"
    pages = {
        feed: _rss(
            ("https://www.example.com/news/old", "Old", "2026-09-30"),
            ("https://www.example.com/news/in", "In", "2026-10-15"),
            ("https://www.example.com/news/future", "Future", "2026-12-01"),
            ("https://www.example.com/news/undated", "Undated", ""),
        )
    }
    config = _config([_route("rss-main", feed)])

    result = _discover(config, _transport(pages))

    assert result.errors == []
    assert result.sitemaps_fetched == 0
    assert [c.url for c in result.candidates] == [
        "https://www.example.com/news/in",
        "https://www.example.com/news/undated",
    ]
    assert [c.published for c in result.candidates] == ["2026-10-15", ""]


def test_rss_archive_uses_only_approved_topic_routes() -> None:
    good = "https://feeds.example.com/archive.xml"
    other_topic = "https://feeds.example.com/other.xml"
    unapproved = "https://feeds.example.com/blocked.xml"
    pages = {
        good: _rss(("https://www.example.com/news/good", "Good", "2026-10-10")),
        other_topic: _rss(("https://www.example.com/news/other", "Other", "2026-10-10")),
        unapproved: _rss(("https://www.example.com/news/blocked", "Blocked", "2026-10-10")),
    }
    routes = [
        _route("rss-good", good),
        _route("rss-other", other_topic, topic_id="other"),
        _route("rss-blocked", unapproved, approved=False),
    ]
    requested: list[str] = []

    result = _discover(_config(routes), _transport(pages, requested))

    assert requested == [good]
    assert result.errors == []
    assert len(result.candidates) == 1
    candidate: HistoricalArticle = result.candidates[0]
    assert candidate.source_id == "archive-news"
    assert candidate.url == "https://www.example.com/news/good"
    assert candidate.title == "Good"
    assert candidate.published == "2026-10-10"
    assert candidate.discovery_url == good


def test_child_sitemap_host_is_rejected_before_fetch() -> None:
    index = f"{HOME}/sitemap.xml"
    evil = "https://evil.example.org/sitemap.xml"
    good = f"{HOME}/sitemap-good.xml"
    pages = {
        index: _sitemapindex(evil, good),
        evil: _urlset(("https://evil.example.org/news/x", "2026-10-05")),
        good: _urlset(("https://www.example.com/news/y", "2026-10-06")),
    }
    requested: list[str] = []
    config = _config([_route("sitemap-main", index, route_type="sitemap")])

    result = _discover(config, _transport(pages, requested))

    assert [c.url for c in result.candidates] == ["https://www.example.com/news/y"]
    assert result.sitemaps_fetched == 2
    assert evil not in requested
    assert any("child sitemap" in error and "evil.example.org" in error for error in result.errors)


def test_dedupe_and_limit() -> None:
    sitemap = f"{HOME}/sitemap.xml"
    pages = {
        sitemap: _urlset(
            ("https://www.example.com/news/a", "2026-10-01"),
            ("https://www.example.com/news/a", "2026-10-02"),
            ("https://www.example.com/news/b", "2026-10-03"),
            ("https://www.example.com/news/c", "2026-10-04"),
        )
    }
    config = _config([_route("sitemap-main", sitemap, route_type="sitemap")])
    transport = _transport(pages)

    deduped = _discover(config, transport, limit=100)
    assert [c.url for c in deduped.candidates] == [
        "https://www.example.com/news/a",
        "https://www.example.com/news/b",
        "https://www.example.com/news/c",
    ]

    limited = _discover(config, transport, limit=2)
    assert [c.url for c in limited.candidates] == [
        "https://www.example.com/news/a",
        "https://www.example.com/news/b",
    ]


def test_unsupported_api_route_and_json_payload_are_errors() -> None:
    api = f"{HOME}/api/articles"
    json_feed = f"{HOME}/feed.json"
    pages = {api: '{"articles": []}', json_feed: '{"items": []}'}
    routes = [
        _route("api-route", api, route_type="api"),
        _route("json-route", json_feed),
    ]

    result = _discover(_config(routes), _transport(pages))

    assert result.candidates == []
    assert any("unsupported route type 'api'" in error for error in result.errors)
    assert any(
        "unsupported discovery payload" in error and "feed.json" in error for error in result.errors
    )


def test_provenance_stamping_through_fetch_url_documents(tmp_path: Path) -> None:
    url = "https://www.example.com/news/archive"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>archive article</html>")

    cache = BlobCache(tmp_path)
    row = _config([]).to_registry_row()
    items = [{"url": url, "title": "Archive story", "published": "2026-10-15"}]

    articles = fetch_url_documents(
        row,
        items,
        policy=POLICY,
        cache=cache,
        transport=httpx.MockTransport(handler),
        provenance="live_feed",
        discovered_via="archive",
    )

    assert len(articles) == 1
    article = articles[0]
    assert article.error == ""
    assert article.provenance == "live_feed"
    assert article.discovered_via == "archive"
    meta = cache.read_meta(row.source_id, article.sha256)
    assert meta["provenance"] == "live_feed"
    assert meta["discovered_via"] == "archive"

    defaults = fetch_url_documents(
        row,
        items,
        policy=POLICY,
        cache=cache,
        transport=httpx.MockTransport(handler),
    )
    assert defaults[0].provenance == "live_bing"
    assert defaults[0].discovered_via == "bing_news"


def test_unusable_row_is_refused_before_any_network_call() -> None:
    feed = "https://feeds.example.com/archive.xml"
    config = _config([_route("rss-main", feed)])
    row = config.to_registry_row().model_copy(update={"enabled": False})
    assert not row.usable
    requested: list[str] = []
    pages = {feed: _rss(("https://www.example.com/news/a", "A", "2026-10-15"))}
    transport = _transport(pages, requested)

    with pytest.raises(SourceNotPermitted, match="not permitted for archive discovery"):
        discover_historical_articles(
            row,
            config,
            start=START,
            end=END,
            topic_id="ukraine",
            policy=POLICY,
            transport=transport,
        )

    assert requested == []
