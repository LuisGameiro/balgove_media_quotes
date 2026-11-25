"""Bounded historical discovery into the blob cache (offline, injected HTTP transport)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx

from media_quotes.adapters import BlobCache
from media_quotes.archive import discover_archive_documents
from media_quotes.config import Settings
from media_quotes.fetch import FetchPolicy
from media_quotes.registry import load_sources_csv

ROOT = Path(__file__).resolve().parents[2]
ROW = next(
    row for row in load_sources_csv(ROOT / "data/sources.csv") if row.source_id == "guardian"
)


def test_sitemap_index_recursion_and_date_filter(tmp_path: Path) -> None:
    index = b"""<sitemapindex><sitemap><loc>https://www.theguardian.com/news.xml</loc></sitemap></sitemapindex>"""
    feed = b"""<urlset><url><loc>https://www.theguardian.com/old</loc><lastmod>2026-10-02</lastmod></url><url><loc>https://www.theguardian.com/new</loc><lastmod>2026-11-20</lastmod></url></urlset>"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("sitemap.xml"):
            return httpx.Response(200, content=index)
        return httpx.Response(200, content=feed)

    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    httpx.AsyncClient = client_factory  # type: ignore[assignment]
    try:
        result = discover_archive_documents(
            ROW,
            "https://www.theguardian.com/sitemap.xml",
            policy=FetchPolicy(requests_per_second=1000),
            topic_pack=None,
            start=date(2026, 10, 1),
            end=date(2026, 10, 31),
            max_articles=10,
            require_topic=False,
            cache=BlobCache(tmp_path / "blobs"),
        )
    finally:
        httpx.AsyncClient = original  # type: ignore[assignment]
    assert result.sitemaps_fetched == 2
    assert result.discovered_urls == 1
    assert result.stored_articles == 1
    assert result.errors == []


def test_sitemap_attempt_cap_includes_failed_fetches(tmp_path: Path) -> None:
    children = "".join(
        f"<sitemap><loc>https://www.theguardian.com/child-{index}.xml</loc></sitemap>"
        for index in range(60)
    )
    index = f"<sitemapindex>{children}</sitemapindex>".encode()
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("sitemap.xml"):
            return httpx.Response(200, content=index)
        return httpx.Response(404)

    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    httpx.AsyncClient = client_factory  # type: ignore[assignment]
    try:
        result = discover_archive_documents(
            ROW,
            "https://www.theguardian.com/sitemap.xml",
            policy=FetchPolicy(requests_per_second=1000),
            topic_pack=None,
            start=date(2026, 10, 1),
            end=date(2026, 10, 31),
            max_sitemaps=5,
            max_articles=10,
            require_topic=False,
            cache=BlobCache(tmp_path / "blobs"),
        )
    finally:
        httpx.AsyncClient = original  # type: ignore[assignment]

    assert len(requests) == 5
    assert result.sitemaps_fetched == 1
    assert len(result.errors) == 4


def test_malformed_feed_xml_is_recorded_as_error(tmp_path: Path) -> None:
    """defusedxml parse failures must be isolated into ArchiveResult.errors."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<rss><item><title>broken</rss>")

    original = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    httpx.AsyncClient = client_factory  # type: ignore[assignment]
    try:
        result = discover_archive_documents(
            ROW,
            "https://www.theguardian.com/rss",
            policy=FetchPolicy(requests_per_second=1000),
            topic_pack=None,
            start=date(2026, 10, 1),
            end=date(2026, 10, 31),
            max_articles=10,
            require_topic=False,
            cache=BlobCache(tmp_path / "blobs"),
        )
    finally:
        httpx.AsyncClient = original  # type: ignore[assignment]

    assert result.errors
    assert result.discovered_urls == 0
    assert result.stored_articles == 0


def test_archive_stage_dry_run_has_no_writes(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        blob_dir=tmp_path / "blobs",
        fixtures_dir=ROOT / "data/fixtures",
        sources_csv=ROOT / "data/sources.csv",
        sources_dir=ROOT / "data/sources",
        sections_csv=ROOT / "data/sections.csv",
        schema_path=ROOT / "data/quotes.schema.json",
        gold_set_dir=ROOT / "data/gold_set",
    )
    assert settings.blob_dir == tmp_path / "blobs"
