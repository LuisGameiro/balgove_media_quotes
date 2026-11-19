"""C1 — rights-gated HTTP fetch policy and offline MockTransport tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from media_quotes.exceptions import SourceNotPermitted
from media_quotes.fetch import FetchPolicy, TokenBucket, assert_fetch_permitted, fetch_bytes
from media_quotes.registry import load_sources_csv

CSV = Path(__file__).resolve().parents[2] / "data/sources.csv"
ROW = next(row for row in load_sources_csv(CSV) if row.source_id == "guardian_api")
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
        FetchPolicy(timeout_seconds="10")


def test_token_bucket_refills_and_limits() -> None:
    bucket = TokenBucket(rate_per_second=1.0)
    assert bucket.allow(0.0)
    assert not bucket.allow(0.5)
    assert bucket.allow(1.5)


def test_t0_gate_and_track_a_approval_required() -> None:
    with pytest.raises(SourceNotPermitted, match="Track A"):
        assert_fetch_permitted(APPROVED, track_a_approved=False)
    with pytest.raises(SourceNotPermitted):
        assert_fetch_permitted(ROW, track_a_approved=True)


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
        )
    )
    assert content == b"article bytes"
    assert len(digest) == 64


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
