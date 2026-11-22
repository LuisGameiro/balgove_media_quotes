"""C1 — live-capable, rights-gated HTTP fetching (docs/12 Phase C).

No source is activated by this module: every call requires both the T0 gate in the registry
row AND an explicit Track A approval flag. Track B ( Reuters/BBC/commercial ) stays blocked
until DEC-14 is recorded. Tests use httpx.MockTransport, so the suite never touches network.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .exceptions import RateLimitExceededError, SourceNotPermitted
from .registry import SourceRegistryRow

if TYPE_CHECKING:
    from .config import Settings

POLITE_USER_AGENT = "MediaQuotesBot/0.1 (+internal research; rights-gated)"


def allowed_hosts_for(row: SourceRegistryRow) -> frozenset[str]:
    """Hosts explicitly registered for this source; never inferred from arbitrary URLs."""
    hosts: set[str] = set()
    for value in (row.homepage_url, row.feed_url):
        host = urlparse(value).hostname
        if host:
            hosts.add(host.lower())
    return frozenset(hosts)


def assert_url_permitted(
    url: str, row: SourceRegistryRow, *, extra_hosts: frozenset[str] = frozenset()
) -> None:
    """HTTPS-only host allowlist; blocks using one source's rights for another's host."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("article URL must be absolute https")
    host = (parsed.hostname or "").lower()
    if not host or host not in (allowed_hosts_for(row) | extra_hosts):
        raise ValueError(f"URL host {host!r} is not registered for source {row.source_id!r}")


class FetchPolicy(BaseModel):
    """Strict, version-pinned HTTP policy; values come from docs/02 §4.1."""

    model_config = ConfigDict(strict=True, extra="forbid")

    timeout_seconds: float = Field(default=10.0, gt=0)
    max_connections: int = Field(default=10, gt=0)
    max_keepalive: int = Field(default=2, gt=0)
    requests_per_second: float = Field(default=1.0, gt=0)
    max_bytes: int = Field(default=5_000_000, gt=0)
    user_agent: str = POLITE_USER_AGENT

    def client_kwargs(self) -> dict[str, Any]:
        """httpx.AsyncClient constructor arguments from this policy."""
        return {
            "timeout": httpx.Timeout(self.timeout_seconds),
            "limits": httpx.Limits(
                max_connections=self.max_connections,
                max_keepalive_connections=self.max_keepalive,
            ),
            "headers": {"User-Agent": self.user_agent},
            "follow_redirects": False,
        }


def fetch_policy_from(settings: Settings) -> FetchPolicy:
    """Build the live fetch policy from settings, keeping the library defaults as fallback."""
    return FetchPolicy(
        timeout_seconds=settings.fetch_timeout_seconds,
        requests_per_second=settings.fetch_requests_per_second,
        max_bytes=settings.fetch_max_bytes,
        max_connections=settings.fetch_max_connections,
    )


@dataclass(slots=True)
class TokenBucket:
    """Deterministic in-memory token bucket; `now` is injectable for offline tests."""

    rate_per_second: float
    capacity: float = 1.0
    tokens: float = 1.0
    last_refill: float = 0.0

    def allow(self, now: float) -> bool:
        elapsed = max(0.0, now - self.last_refill)
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate_per_second)
        self.last_refill = now
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True


def assert_fetch_permitted(row: SourceRegistryRow, *, track_a_approved: bool) -> None:
    """T0 gate + explicit Track A approval: refuses every unapproved source."""
    if not track_a_approved:
        raise SourceNotPermitted(f"source '{row.source_id}' has no Track A approval (A4)")
    if not row.usable:
        raise SourceNotPermitted(
            f"source '{row.source_id}' is not usable: enabled={row.enabled}, "
            f"rights={row.rights_status}, access={row.access_mode}"
        )


async def _read_response(response: httpx.Response, max_bytes: int) -> bytes:
    """Stream a response under a hard byte cap, before and during download."""
    response.raise_for_status()
    declared = response.headers.get("content-length")
    if declared and int(declared) > max_bytes:
        raise ValueError(f"response too large: {declared} > {max_bytes}")
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"response too large: {total} > {max_bytes}")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_bytes(
    url: str,
    *,
    row: SourceRegistryRow,
    policy: FetchPolicy,
    track_a_approved: bool,
    bucket: TokenBucket | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] | float | None = None,
    allowed_hosts: frozenset[str] = frozenset(),
    client: httpx.AsyncClient | None = None,
) -> tuple[bytes, str]:
    """Fetch raw article bytes and return (bytes, sha256). Never used without approval."""
    assert_fetch_permitted(row, track_a_approved=track_a_approved)
    assert_url_permitted(url, row, extra_hosts=allowed_hosts)
    token_bucket = bucket or TokenBucket(policy.requests_per_second)

    if callable(clock):
        now = clock()
    elif clock is not None:
        now = float(clock)
    else:
        now = time.monotonic()

    if not token_bucket.allow(now):
        if clock is not None:
            raise RateLimitExceededError("rate limit exceeded")
        # Real fetches wait for a token; injected clocks stay deterministic.
        deadline = time.monotonic() + max(1.0, 2.0 / token_bucket.rate_per_second)
        while time.monotonic() < deadline:
            time.sleep(min(0.05, 1.0 / token_bucket.rate_per_second))
            if token_bucket.allow(time.monotonic()):
                break
        else:
            raise RateLimitExceededError("rate limit exceeded")

    if client is not None:
        async with client.stream("GET", url) as response:
            content = await _read_response(response, policy.max_bytes)
    else:
        async with (
            httpx.AsyncClient(transport=transport, **policy.client_kwargs()) as owned,
            owned.stream("GET", url) as response,
        ):
            content = await _read_response(response, policy.max_bytes)
    return content, hashlib.sha256(content).hexdigest()
