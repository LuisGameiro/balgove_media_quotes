"""C1 — live-capable, rights-gated HTTP fetching (docs/12 Phase C).

No source is activated by this module: every call requires both the T0 gate in the registry
row AND an explicit Track A approval flag. Track B ( Reuters/BBC/commercial ) stays blocked
until DEC-14 is recorded. Tests use httpx.MockTransport, so the suite never touches network.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

from .exceptions import SourceNotPermitted
from .registry import SourceRegistryRow

POLITE_USER_AGENT = "MediaQuotesBot/0.1 (+internal research; rights-gated)"


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


async def fetch_bytes(
    url: str,
    *,
    row: SourceRegistryRow,
    policy: FetchPolicy,
    track_a_approved: bool,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Any = None,
) -> tuple[bytes, str]:
    """Fetch raw article bytes and return (bytes, sha256). Never used without approval."""
    assert_fetch_permitted(row, track_a_approved=track_a_approved)
    if not url.startswith(("https://", "http://")):
        raise ValueError("article URL must be absolute http(s)")
    bucket = TokenBucket(policy.requests_per_second)
    import time as _time

    now = (clock or _time.monotonic)()
    if not bucket.allow(now):
        raise RuntimeError("rate limit exceeded")
    async with httpx.AsyncClient(transport=transport, **policy.client_kwargs()) as client:
        response = await client.get(url)
        response.raise_for_status()
        content = response.content
        if len(content) > policy.max_bytes:
            raise ValueError(f"response too large: {len(content)} > {policy.max_bytes}")
        return content, hashlib.sha256(content).hexdigest()
