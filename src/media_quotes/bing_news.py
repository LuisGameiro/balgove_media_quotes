"""Free Bing News RSS discovery for original publisher URLs.

Bing supplies discovery metadata only. Article text must still be fetched from the original
publisher through the normal rights gate.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx
from defusedxml.ElementTree import ParseError, fromstring

_DISCOVERY_URL = "https://www.bing.com/news/search"


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


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
) -> list[dict[str, str]]:
    """Return current original-publisher URLs discovered through Bing News RSS."""
    if limit < 1:
        raise ValueError("limit must be positive")
    normalized_domain = domain.strip().lower()
    if not normalized_domain:
        raise ValueError("domain must not be empty")

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0),
        follow_redirects=False,
        transport=transport,
        headers={"User-Agent": "MediaQuotesProof/1.0"},
    ) as client:
        response = await client.get(
            _DISCOVERY_URL,
            params={"q": f"site:{normalized_domain} {query}", "format": "rss"},
        )
        response.raise_for_status()
        if len(response.content) > 5_000_000:
            raise ValueError("Bing News RSS response exceeds size limit")

    try:
        root: Any = fromstring(response.content)
    except ParseError as exc:
        raise ValueError("Bing News returned invalid XML") from exc

    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in root.iter():
        if _local_tag(str(item.tag)) not in {"item", "entry"}:
            continue
        title = ""
        link = ""
        published = ""
        for child in item:
            tag = _local_tag(str(child.tag))
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
