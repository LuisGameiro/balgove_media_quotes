"""Free Bing News RSS discovery for original publisher URLs.

Bing supplies discovery metadata only. Article text must still be fetched from the original
publisher through the normal rights gate.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from defusedxml.ElementTree import ParseError, fromstring

from ..exceptions import RateLimitExceededError
from ..text_utils import local_xml_tag
from .fetch import TokenBucket

_DISCOVERY_URL = "https://www.bing.com/news/search"
_MAX_BYTES = 5_000_000


def _decode_target(link: str) -> str:
    parsed = urlparse(link)
    if parsed.hostname and parsed.hostname.endswith("bing.com"):
        targets = parse_qs(parsed.query).get("url")
        if targets:
            return urljoin(link, targets[0])
    return link


async def discover_bing_news(
    domain: str,
    *,
    query: str = "Ukraine",
    limit: int = 5,
    transport: httpx.AsyncBaseTransport | None = None,
    bucket: TokenBucket | None = None,
    clock: Callable[[], float] | float | None = None,
) -> list[dict[str, str]]:
    """Return current original-publisher URLs discovered through Bing News RSS."""
    if limit < 1:
        raise ValueError("limit must be positive")
    normalized_domain = domain.strip().lower()
    if not normalized_domain:
        raise ValueError("domain must not be empty")

    effective_bucket = bucket or TokenBucket(1.0)
    if callable(clock):
        now = clock()
    elif clock is not None:
        now = float(clock)
    else:
        now = time.monotonic()
    if not effective_bucket.allow(now):
        if clock is not None:
            raise RateLimitExceededError("rate limit exceeded")
        # Real fetches wait for a token; injected clocks stay deterministic.
        deadline = time.monotonic() + max(1.0, 2.0 / effective_bucket.rate_per_second)
        while time.monotonic() < deadline:
            await asyncio.sleep(min(0.05, 1.0 / effective_bucket.rate_per_second))
            if effective_bucket.allow(time.monotonic()):
                break
        else:
            raise RateLimitExceededError("rate limit exceeded")

    async with (
        httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "MediaQuotesProof/1.0"},
        ) as client,
        client.stream(
            "GET",
            _DISCOVERY_URL,
            params={"q": f"site:{normalized_domain} {query}", "format": "rss"},
        ) as response,
    ):
        response.raise_for_status()
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > _MAX_BYTES:
                raise ValueError("Bing News RSS response exceeds size limit")
            chunks.append(chunk)
        content = b"".join(chunks)

    try:
        root: Any = fromstring(content)
    except ParseError as exc:
        raise ValueError("Bing News returned invalid XML") from exc

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in root.iter():
        if local_xml_tag(str(item.tag)).lower() not in {"item", "entry"}:
            continue
        title = ""
        link = ""
        published = ""
        for child in item:
            tag = local_xml_tag(str(child.tag)).lower()
            text = (child.text or "").strip()
            if tag == "title" and not title:
                title = text
            elif tag == "link" and not link:
                link = child.attrib.get("href", "") or text
            elif tag in {"pubdate", "published", "updated", "date"} and not published:
                published = text
        if not link:
            continue
        target = _decode_target(link.strip())
        parsed = urlparse(target)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host:
            continue
        if host != normalized_domain and not host.endswith(f".{normalized_domain}"):
            continue
        if target in seen:
            continue
        seen.add(target)
        results.append({"title": title, "url": target, "published": published})
        if len(results) >= limit:
            break
    return results
