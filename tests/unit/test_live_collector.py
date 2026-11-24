"""Pass 2 collector: official feed first, opt-in Bing discovery only as a fallback."""

from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

import media_quotes.live as live
from media_quotes.adapters import BlobCache
from media_quotes.exceptions import SourceNotPermitted
from media_quotes.fetch import FetchPolicy
from media_quotes.live import (
    FetchedArticle,
    LiveSourceResult,
    collect_live_source,
    fetch_url_documents,
)
from media_quotes.registry import SourceRegistryRow
from media_quotes.source_configs import load_source_config

BBC = load_source_config("bbc")
ROW: SourceRegistryRow = BBC.to_registry_row()
POLICY = FetchPolicy(requests_per_second=1000.0)


@pytest.fixture(autouse=True)
def _global_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """check_dns=True must stay offline; resolve every host to a global IP."""

    def fake_getaddrinfo(host: str, port: int, **kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


def _article(url: str, *, error: str = "") -> FetchedArticle:
    return FetchedArticle(
        url=url,
        title="Story",
        raw=b"" if error else b"article bytes",
        sha256="" if error else "abc",
        blob_path="" if error else "blob.html",
        error=error,
        provenance="live_bing",
        discovered_via="bing_news",
    )


def test_fetched_article_provenance_and_blob_sidecar(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>article bytes</html>")

    cache = BlobCache(tmp_path)
    good_url = "https://www.bbc.co.uk/news/one"
    items = [
        {"title": "BBC story", "url": good_url, "published": "2026-11-24"},
        {"title": "Bad story", "url": "https://unregistered.example/x", "published": ""},
    ]
    articles = fetch_url_documents(
        ROW,
        items,
        policy=POLICY,
        cache=cache,
        extra_hosts=frozenset({"www.bbc.co.uk"}),
        transport=httpx.MockTransport(handler),
    )

    assert len(articles) == 2
    good, bad = articles
    assert good.error == ""
    assert good.provenance == "live_bing"
    assert good.discovered_via == "bing_news"
    assert good.raw == b"<html>article bytes</html>"
    assert Path(good.blob_path).read_bytes() == good.raw
    meta = cache.read_meta(ROW.source_id, good.sha256)
    assert meta["url"] == good_url
    assert meta["title"] == "BBC story"
    assert meta["published"] == "2026-11-24"
    assert meta["source_id"] == ROW.source_id
    assert meta["provenance"] == "live_bing"
    assert meta["discovered_via"] == "bing_news"

    # An unregistered host becomes a per-article error instead of aborting the batch.
    assert "not registered" in bad.error
    assert bad.provenance == "live_bing"
    assert bad.discovered_via == "bing_news"


def test_official_success_never_calls_bing(monkeypatch: pytest.MonkeyPatch) -> None:
    good = FetchedArticle(
        url="https://feeds.bbci.co.uk/news/one",
        title="Official story",
        raw=b"official",
        sha256="abc",
        blob_path="blob.html",
    )
    seen: list[str] = []

    def fake_feed(
        *args: object, **kwargs: object
    ) -> tuple[list[dict[str, str]], list[FetchedArticle]]:
        seen.append("feed")
        return ([{"url": good.url, "title": good.title}], [good])

    def fake_bing(*args: object, **kwargs: object) -> list[dict[str, str]]:
        raise AssertionError("Bing discovery must not run after a successful official feed")

    monkeypatch.setattr(live, "fetch_feed_documents", fake_feed)
    monkeypatch.setattr(live, "discover_bing_news", fake_bing)

    result = collect_live_source(
        ROW,
        feed_url=BBC.feed_url,
        topic_pack=None,
        policy=POLICY,
        limit=5,
        source_config=BBC,
    )

    assert seen == ["feed"]
    assert isinstance(result, LiveSourceResult)
    assert result.provenance == "live_feed"
    assert result.route == "official_feed"
    assert result.articles == [good]
    assert result.articles[0].provenance == "live_feed"
    assert result.articles[0].discovered_via == "official_feed"
    assert result.errors == []


def test_bing_fallback_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_feed(
        *args: object, **kwargs: object
    ) -> tuple[list[dict[str, str]], list[FetchedArticle]]:
        return (
            [{"url": "https://feeds.bbci.co.uk/news/rss.xml", "title": "Official story"}],
            [_article("https://feeds.bbci.co.uk/news/rss.xml", error="feed unavailable")],
        )

    bing_calls: list[str] = []
    bing_item = {
        "title": "Bing story",
        "url": "https://www.bbc.co.uk/news/bing",
        "published": "2026-11-24",
    }

    async def fake_bing(domain: str, **kwargs: object) -> list[dict[str, str]]:
        bing_calls.append(domain)
        return [bing_item]

    fetched: list[list[dict[str, str]]] = []

    def fake_url_docs(
        row: SourceRegistryRow, items: list[dict[str, str]], **kwargs: object
    ) -> list[FetchedArticle]:
        fetched.append(items)
        return [_article(items[0]["url"])]

    monkeypatch.setattr(live, "fetch_feed_documents", fake_feed)
    monkeypatch.setattr(live, "discover_bing_news", fake_bing)
    monkeypatch.setattr(live, "fetch_url_documents", fake_url_docs)

    disabled = BBC.model_copy(update={"bing_discovery_enabled": False})
    result = collect_live_source(
        ROW,
        feed_url=BBC.feed_url,
        topic_pack=None,
        policy=POLICY,
        limit=5,
        source_config=disabled,
    )
    assert bing_calls == []
    assert fetched == []
    assert result.provenance == "live_feed"
    assert result.articles[0].error == "feed unavailable"

    enabled = BBC.model_copy(update={"bing_discovery_enabled": True})
    result = collect_live_source(
        ROW,
        feed_url=BBC.feed_url,
        topic_pack=None,
        policy=POLICY,
        limit=5,
        source_config=enabled,
    )
    assert bing_calls == ["www.bbc.co.uk"]
    assert fetched == [[bing_item]]
    assert result.provenance == "live_bing"
    assert result.route == "bing_news"
    assert result.discovered == [bing_item]
    assert result.articles[0].provenance == "live_bing"


def test_unregistered_bing_candidate_is_dropped_and_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_feed(
        *args: object, **kwargs: object
    ) -> tuple[list[dict[str, str]], list[FetchedArticle]]:
        return ([], [])

    async def fake_bing(domain: str, **kwargs: object) -> list[dict[str, str]]:
        return [
            {"title": "kept", "url": "https://www.bbc.co.uk/news/kept", "published": ""},
            {"title": "dropped", "url": "https://news.bbc.co.uk/news/dropped", "published": ""},
        ]

    fetched: list[list[dict[str, str]]] = []

    def fake_url_docs(
        row: SourceRegistryRow, items: list[dict[str, str]], **kwargs: object
    ) -> list[FetchedArticle]:
        fetched.append(items)
        return [_article(items[0]["url"])]

    monkeypatch.setattr(live, "fetch_feed_documents", fake_feed)
    monkeypatch.setattr(live, "discover_bing_news", fake_bing)
    monkeypatch.setattr(live, "fetch_url_documents", fake_url_docs)

    config = BBC.model_copy(
        update={"bing_discovery_enabled": True, "content_hosts": ["www.bbc.co.uk"]}
    )
    result = collect_live_source(
        ROW,
        feed_url=BBC.feed_url,
        topic_pack=None,
        policy=POLICY,
        limit=5,
        source_config=config,
    )

    assert len(fetched) == 1
    assert [item["url"] for item in fetched[0]] == ["https://www.bbc.co.uk/news/kept"]
    assert result.provenance == "live_bing"
    assert any("news.bbc.co.uk" in error for error in result.errors)


def test_unusable_row_raises_source_not_permitted() -> None:
    unusable = ROW.model_copy(update={"enabled": False})
    assert not unusable.usable
    with pytest.raises(SourceNotPermitted):
        collect_live_source(
            unusable,
            feed_url=BBC.feed_url,
            topic_pack=None,
            policy=POLICY,
            limit=5,
            source_config=BBC,
        )
