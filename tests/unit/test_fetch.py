"""C1 — rights-gated HTTP fetch policy and offline MockTransport tests."""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from media_quotes.exceptions import SourceNotPermitted
from media_quotes.fetch import (
    FetchPolicy,
    TokenBucket,
    allowed_hosts_for,
    assert_fetch_permitted,
    assert_url_permitted,
    fetch_bytes,
)
from media_quotes.registry import load_sources_csv

CSV = Path(__file__).resolve().parents[2] / "data/sources.csv"
ROW = next(row for row in load_sources_csv(CSV) if row.source_id == "guardian_api")
UNAPPROVED = ROW.model_copy(
    update={
        "enabled": False,
        "rights_status": "pending_counsel_review",
        "access_mode": "pending_review",
        "license_reference": None,
    }
)
APPROVED = ROW.model_copy(
    update={
        "enabled": True,
        "rights_status": "api_permitted",
        "license_reference": "TRACK-A-TEST",
    }
)


def test_policy_is_strict_and_generates_client_kwargs() -> None:
    policy = FetchPolicy()
    kwargs = policy.client_kwargs()
    assert isinstance(kwargs["timeout"], httpx.Timeout)
    assert kwargs["limits"].max_connections == policy.max_connections
    assert kwargs["headers"]["User-Agent"].startswith("MediaQuotesBot/")
    assert kwargs["follow_redirects"] is False
    with pytest.raises(ValidationError):
        FetchPolicy.model_validate({"timeout_seconds": "10"})
    policy = FetchPolicy()
    kwargs = policy.client_kwargs()
    assert isinstance(kwargs["timeout"], httpx.Timeout)
    assert kwargs["limits"].max_connections == policy.max_connections
    assert kwargs["headers"]["User-Agent"].startswith("MediaQuotesBot/")
    assert kwargs["follow_redirects"] is False
    with pytest.raises(ValidationError):
        FetchPolicy(timeout_seconds="10")


def test_token_bucket_refills_and_limits() -> None:
    bucket = TokenBucket(rate_per_second=1.0)
    assert bucket.allow(0.0)
    assert not bucket.allow(0.5)
    assert bucket.allow(1.5)


def test_feed_documents_share_one_rate_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    import media_quotes.live as live

    feed = (
        b"<rss><channel><item><link>https://example.invalid/a</link>"
        b"<title>Article</title></item></channel></rss>"
    )
    seen: list[object] = []

    async def fake_fetch(url: str, **kwargs: object) -> tuple[bytes, str]:
        seen.append(kwargs["bucket"])
        return (feed if url.endswith("/feed") else b"article"), "abc"

    class FakeCache:
        def store(self, source_id: str, raw: bytes) -> tuple[str, Path]:
            return "abc", Path("blob.html")

    monkeypatch.setattr(live, "fetch_bytes", fake_fetch)
    live.fetch_feed_documents(
        APPROVED,
        "https://example.invalid/feed",
        policy=FetchPolicy(),
        topic_pack=None,
        require_topic=False,
        limit=1,
        extra_hosts=frozenset({"example.invalid"}),
        cache=FakeCache(),
    )
    assert len(seen) == 2
    assert seen[0] is seen[1]


def test_feed_documents_resolve_relative_article_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    import media_quotes.live as live

    feed = b"<rss><channel><item><link>/article</link><title>Article</title></item></channel></rss>"
    seen: list[str] = []

    async def fake_fetch(url: str, **kwargs: object) -> tuple[bytes, str]:
        seen.append(url)
        return (feed if url.endswith("/feed") else b"article"), "abc"

    class FakeCache:
        def store(self, source_id: str, raw: bytes) -> tuple[str, Path]:
            return "abc", Path("blob.html")

        def store_meta(self, source_id: str, digest: str, metadata: object) -> Path:
            return Path("blob.meta")

    monkeypatch.setattr(live, "fetch_bytes", fake_fetch)
    live.fetch_feed_documents(
        APPROVED,
        "https://example.invalid/feed",
        policy=FetchPolicy(),
        topic_pack=None,
        require_topic=False,
        limit=1,
        extra_hosts=frozenset({"example.invalid"}),
        cache=FakeCache(),
    )

    assert seen == ["https://example.invalid/feed", "https://example.invalid/article"]


def test_fetch_bytes_can_follow_same_host_redirects_when_enabled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/article"})
        return httpx.Response(200, content=b"article bytes")

    transport = httpx.MockTransport(handler)
    content, digest = asyncio.run(
        fetch_bytes(
            "https://example.invalid/start",
            row=APPROVED,
            policy=FetchPolicy(follow_redirects=True),
            track_a_approved=True,
            transport=transport,
            clock=lambda: 10.0,
            allowed_hosts=frozenset({"example.invalid"}),
        )
    )

    assert content == b"article bytes"
    assert len(digest) == 64


def test_fetch_bytes_rejects_cross_host_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://other.invalid/article"})

    transport = httpx.MockTransport(handler)
    with pytest.raises(ValueError, match="not registered"):
        asyncio.run(
            fetch_bytes(
                "https://example.invalid/start",
                row=APPROVED,
                policy=FetchPolicy(follow_redirects=True),
                track_a_approved=True,
                transport=transport,
                clock=lambda: 10.0,
                allowed_hosts=frozenset({"example.invalid"}),
            )
        )


def test_t0_gate_and_track_a_approval_required() -> None:
    with pytest.raises(SourceNotPermitted, match="Track A"):
        assert_fetch_permitted(APPROVED, track_a_approved=False)
    with pytest.raises(SourceNotPermitted):
        assert_fetch_permitted(UNAPPROVED, track_a_approved=True)


def test_fetch_bytes_uses_mock_transport_and_returns_hash() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"].startswith("MediaQuotesBot/")
        return httpx.Response(200, content=b"article bytes")

    transport = httpx.MockTransport(handler)
    content, digest = asyncio.run(
        fetch_bytes(
            "https://example.invalid/article",
            row=APPROVED,
            policy=FetchPolicy(),
            track_a_approved=True,
            transport=transport,
            clock=lambda: 10.0,
            allowed_hosts=frozenset({"example.invalid"}),
        )
    )
    assert content == b"article bytes"
    assert len(digest) == 64


def test_fetch_bytes_reuses_a_supplied_client() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"shared client")

    transport = httpx.MockTransport(handler)
    policy = FetchPolicy()

    async def run() -> tuple[bytes, str]:
        async with httpx.AsyncClient(transport=transport) as client:
            first = await fetch_bytes(
                "https://example.invalid/a",
                row=APPROVED,
                policy=policy,
                track_a_approved=True,
                client=client,
                allowed_hosts=frozenset({"example.invalid"}),
            )
            second = await fetch_bytes(
                "https://example.invalid/b",
                row=APPROVED,
                policy=policy,
                track_a_approved=True,
                client=client,
                allowed_hosts=frozenset({"example.invalid"}),
            )
            return first[0] + second[0], first[1]

    content, digest = asyncio.run(run())
    assert content == b"shared clientshared client"
    assert len(seen) == 2
    assert len(digest) == 64


def test_shared_bucket_waits_for_next_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"ok")

    bucket = TokenBucket(rate_per_second=100.0)  # fast refill keeps the test quick
    results = []
    for url in ("https://example.invalid/a", "https://example.invalid/b"):
        results.append(
            asyncio.run(
                fetch_bytes(
                    url,
                    row=APPROVED,
                    policy=FetchPolicy(requests_per_second=100.0),
                    track_a_approved=True,
                    bucket=bucket,
                    transport=httpx.MockTransport(handler),
                    allowed_hosts=frozenset({"example.invalid"}),
                )
            )
        )
    assert [content for content, _ in results] == [b"ok", b"ok"]


def test_fetch_refuses_oversized_and_relative_urls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 20)

    transport = httpx.MockTransport(handler)
    with pytest.raises(ValueError, match="too large"):
        asyncio.run(
            fetch_bytes(
                "https://example.invalid/a",
                row=APPROVED,
                policy=FetchPolicy(max_bytes=5),
                track_a_approved=True,
                transport=transport,
                clock=lambda: 10.0,
                allowed_hosts=frozenset({"example.invalid"}),
            )
        )
    with pytest.raises(ValueError, match="absolute"):
        asyncio.run(
            fetch_bytes(
                "relative",
                row=APPROVED,
                policy=FetchPolicy(),
                track_a_approved=True,
                transport=transport,
                clock=lambda: 10.0,
            )
        )


def test_allowed_hosts_for_with_extra_urls() -> None:
    hosts = allowed_hosts_for(
        APPROVED,
        extra_urls=["https://feeds.guardian.co.uk/rss", "https://api.guardian.co.uk"],
    )
    assert "feeds.guardian.co.uk" in hosts
    assert "api.guardian.co.uk" in hosts


def test_assert_url_permitted_security_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    extra = frozenset({"example.invalid"})
    # Non-https
    with pytest.raises(ValueError, match="absolute https"):
        assert_url_permitted("http://example.invalid/test", APPROVED, extra_hosts=extra)

    # Non-443 port
    with pytest.raises(ValueError, match="URL port"):
        assert_url_permitted("https://example.invalid:8443/test", APPROVED, extra_hosts=extra)

    # Credentials in URL
    with pytest.raises(ValueError, match="embedded user credentials"):
        assert_url_permitted("https://user:pass@example.invalid/test", APPROVED, extra_hosts=extra)

    # Direct non-global IP
    with pytest.raises(ValueError, match="non-global IP address"):
        assert_url_permitted(
            "https://127.0.0.1/test", APPROVED, extra_hosts=frozenset({"127.0.0.1"})
        )

    # Unregistered host
    with pytest.raises(ValueError, match="not registered"):
        assert_url_permitted("https://unregistered.domain/test", APPROVED)

    # DNS check returning private IP
    def fake_getaddrinfo_private(
        host: str, port: int, **kwargs: object
    ) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo_private)
    with pytest.raises(ValueError, match="resolves to non-global IP"):
        assert_url_permitted(
            "https://example.invalid/test", APPROVED, extra_hosts=extra, check_dns=True
        )

    # DNS check failure (gaierror)
    def fake_getaddrinfo_error(host: str, port: int, **kwargs: object) -> list[tuple[object, ...]]:
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo_error)
    with pytest.raises(ValueError, match="Failed to resolve host"):
        assert_url_permitted(
            "https://example.invalid/test", APPROVED, extra_hosts=extra, check_dns=True
        )

    # DNS check returning valid global IP
    def fake_getaddrinfo_global(host: str, port: int, **kwargs: object) -> list[tuple[object, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo_global)
    assert_url_permitted(
        "https://example.invalid/test", APPROVED, extra_hosts=extra, check_dns=True
    )
