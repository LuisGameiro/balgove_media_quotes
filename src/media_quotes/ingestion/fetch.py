"""C1 — live-capable, rights-gated HTTP fetching.

No source is activated by this module: every call requires both the T0 gate in the registry
row AND an explicit Track A approval flag. Track B ( Reuters/BBC/commercial ) stays blocked
until DEC-14 is recorded. Tests use httpx.MockTransport, so the suite never touches network.
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import socket
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..exceptions import RateLimitExceededError, SourceNotPermitted
from ..registry import SourceRegistryRow

if TYPE_CHECKING:
    from ..config import Settings

POLITE_USER_AGENT = "MediaQuotesBot/0.1 (+internal research; rights-gated)"


def allowed_hosts_for(row: SourceRegistryRow, extra_urls: Iterable[str] = ()) -> frozenset[str]:
    """Hosts explicitly registered for this source; never inferred from arbitrary URLs."""
    hosts: set[str] = set()
    for value in (row.homepage_url, row.feed_url, *extra_urls):
        host = urlparse(value).hostname
        if host:
            hosts.add(host.lower())
    return frozenset(hosts)


def _validate_resolved_ips(host: str, addr_info: Iterable[tuple[Any, ...]]) -> None:
    """Reject any resolved address that is not globally routable."""
    for item in addr_info:
        resolved_ip_str = item[4][0]
        resolved_ip = ipaddress.ip_address(resolved_ip_str)
        if resolved_ip.version == 6 and resolved_ip.ipv4_mapped:
            resolved_ip = resolved_ip.ipv4_mapped
        if not resolved_ip.is_global:
            raise ValueError(f"URL host {host!r} resolves to non-global IP {resolved_ip_str}")


def _pinned_ip(addr_info: list[tuple[Any, ...]]) -> str | None:
    """Pick the validated address to connect to, folding v4-mapped v6 back to v4."""
    if not addr_info:
        return None
    resolved = ipaddress.ip_address(addr_info[0][4][0])
    if resolved.version == 6 and resolved.ipv4_mapped:
        resolved = resolved.ipv4_mapped
    return f"[{resolved.compressed}]" if resolved.version == 6 else resolved.compressed


def _url_with_host(url: str, host: str) -> str:
    """Replace the URL authority with a pinned IP, keeping path/query/fragment intact."""
    parsed = urlparse(url)
    netloc = host if parsed.port is None else f"{host}:{parsed.port}"
    return urlunparse(parsed._replace(netloc=netloc))


def assert_url_permitted(
    url: str,
    row: SourceRegistryRow,
    *,
    extra_hosts: frozenset[str] = frozenset(),
    check_dns: bool = False,
    addr_info: Iterable[tuple[Any, ...]] | None = None,
) -> None:
    """HTTPS-only host allowlist; blocks using one source's rights for another's host."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("article URL must be absolute https")
    if parsed.port is not None and parsed.port != 443:
        raise ValueError(f"URL port {parsed.port} is not permitted; standard HTTPS (443) only")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("URL must not contain embedded user credentials")
    host = (parsed.hostname or "").lower()
    if not host or host not in (allowed_hosts_for(row) | extra_hosts):
        raise ValueError(f"URL host {host!r} is not registered for source {row.source_id!r}")
    try:
        ip = ipaddress.ip_address(host)
        if not ip.is_global:
            raise ValueError(f"URL host {host!r} is a non-global IP address")
    except ValueError as exc:
        if "is a non-global IP address" in str(exc):
            raise
        # Host is a domain name: use pre-resolved addresses when supplied, otherwise resolve
        # synchronously (kept for existing sync callers).
        if addr_info is not None:
            _validate_resolved_ips(host, addr_info)
        elif check_dns:
            try:
                resolved = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            except socket.gaierror as dns_err:
                raise ValueError(f"Failed to resolve host {host!r}: {dns_err}") from dns_err
            _validate_resolved_ips(host, resolved)


async def assert_url_permitted_async(
    url: str,
    row: SourceRegistryRow,
    *,
    extra_hosts: frozenset[str] = frozenset(),
    check_dns: bool = False,
) -> str | None:
    """Async allowlist check; returns the validated IP the caller must pin the connection to."""
    assert_url_permitted(url, row, extra_hosts=extra_hosts, check_dns=False)
    if not check_dns:
        return None
    host = (urlparse(url).hostname or "").lower()
    try:
        ipaddress.ip_address(host)
        return None  # literal address already validated above; no DNS needed
    except ValueError:
        pass

    def resolve() -> list[tuple[Any, ...]]:
        return socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)

    loop = asyncio.get_running_loop()
    try:
        addr_info = await loop.run_in_executor(None, resolve)
    except socket.gaierror as dns_err:
        raise ValueError(f"Failed to resolve host {host!r}: {dns_err}") from dns_err
    assert_url_permitted(url, row, extra_hosts=extra_hosts, addr_info=addr_info)
    return _pinned_ip(addr_info)


class FetchPolicy(BaseModel):
    """Strict, version-pinned HTTP policy; values come from docs/02 §4.1."""

    model_config = ConfigDict(strict=True, extra="forbid")

    timeout_seconds: float = Field(default=10.0, gt=0)
    max_connections: int = Field(default=10, gt=0)
    max_keepalive: int = Field(default=2, gt=0)
    requests_per_second: float = Field(default=1.0, gt=0)
    max_bytes: int = Field(default=5_000_000, gt=0)
    user_agent: str = POLITE_USER_AGENT
    follow_redirects: bool = False
    max_redirects: int = Field(default=3, ge=0, le=10)

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

    def __post_init__(self) -> None:
        if self.rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")

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
    if 300 <= response.status_code < 400:
        # httpx does not consistently treat 3xx as errors; reject explicitly.
        raise ValueError(
            f"redirect response (HTTP {response.status_code}) from {response.request.url} "
            "was not followed; redirects are not permitted"
        )
    response.raise_for_status()
    declared = response.headers.get("content-length")
    if declared:
        try:
            if int(declared) > max_bytes:
                raise ValueError(f"response too large: {declared} > {max_bytes}")
        except (ValueError, TypeError) as exc:
            if "response too large" in str(exc):
                raise
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"response too large: {total} > {max_bytes}")
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_with_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    row: SourceRegistryRow,
    policy: FetchPolicy,
    allowed_hosts: frozenset[str],
    check_dns: bool,
    redirect_hosts: frozenset[str] = frozenset(),
    pin_dns: bool = False,
) -> bytes:
    current_url = url
    for hop in range(policy.max_redirects + 1):
        # Redirect-only hosts never widen the FIRST request's allowlist; they become
        # eligible only on later hops requested by the server.
        hop_hosts = allowed_hosts if hop else allowed_hosts - redirect_hosts
        pinned_ip = await assert_url_permitted_async(
            current_url, row, extra_hosts=hop_hosts, check_dns=check_dns
        )
        stream_kwargs: dict[str, Any] = {}
        if pin_dns and pinned_ip:
            parsed = urlparse(current_url)
            # Connect to the validated IP, but keep TLS SNI and Host on the original name.
            stream_kwargs = {
                "headers": {"Host": parsed.netloc},
                "extensions": {"sni_hostname": parsed.hostname or ""},
            }
            stream_url = _url_with_host(current_url, pinned_ip)
        else:
            stream_url = current_url
        async with client.stream(
            "GET",
            stream_url,
            follow_redirects=False,
            timeout=httpx.Timeout(policy.timeout_seconds),
            **stream_kwargs,
        ) as response:
            location = response.headers.get("location", "")
            if (
                policy.follow_redirects
                and response.status_code in {301, 302, 303, 307, 308}
                and location
            ):
                current_url = urljoin(current_url, location)
                continue
            return await _read_response(response, policy.max_bytes)
    raise ValueError(f"too many redirects while fetching {url}")


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
    check_dns: bool = False,
    redirect_hosts: frozenset[str] = frozenset(),
) -> tuple[bytes, str]:
    """Fetch raw article bytes and return (bytes, sha256). Never used without approval."""
    assert_fetch_permitted(row, track_a_approved=track_a_approved)
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
            await asyncio.sleep(min(0.05, 1.0 / token_bucket.rate_per_second))
            if token_bucket.allow(time.monotonic()):
                break
        else:
            raise RateLimitExceededError("rate limit exceeded")

    # Pin whenever a real network client is used; injected transports (offline tests) are exempt.
    pin_dns = check_dns and transport is None
    if client is not None:
        content = await _read_with_redirects(
            client,
            url,
            row=row,
            policy=policy,
            allowed_hosts=allowed_hosts,
            check_dns=check_dns,
            redirect_hosts=redirect_hosts,
            pin_dns=pin_dns,
        )
    else:
        async with httpx.AsyncClient(transport=transport, **policy.client_kwargs()) as owned:
            content = await _read_with_redirects(
                owned,
                url,
                row=row,
                policy=policy,
                allowed_hosts=allowed_hosts,
                check_dns=check_dns,
                redirect_hosts=redirect_hosts,
                pin_dns=pin_dns,
            )
    return content, hashlib.sha256(content).hexdigest()
