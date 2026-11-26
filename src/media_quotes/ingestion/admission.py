"""Admission-only live probe for sources that have not been approved yet.

Unlike the rights-gated collection paths, this module never calls ``fetch_bytes`` or the T0
gate: nothing here grants rights. Network access is limited to robots.txt, one configured
feed/sitemap and (only when robots allows it) one sample article, using the same FetchPolicy
client settings and size caps as approved fetches.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

import httpx

from ..adapters import parse_rss_feed, parse_sitemap_xml
from ..processing.export import atomic_write_text
from ..source_configs import probe_source_admission, robots_allows
from .fetch import _url_with_host, assert_url_permitted_async, fetch_policy_from

if TYPE_CHECKING:
    from ..config import Settings
    from ..source_configs import SourceConfig

__all__ = ["probe_source_live"]


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _configured_feed_url(config: SourceConfig, topic_id: str) -> str:
    """Resolve the configured feed/sitemap, including unapproved scaffold routes."""
    url = config.discovery_feed_url(topic_id)
    if url:
        return url
    if config.extractor_hints.default_feed_url:
        return config.extractor_hints.default_feed_url
    for route in config.routes_for_topic(topic_id):
        if route.discovery_url:
            return route.discovery_url
    return ""


def probe_source_live(
    config: SourceConfig,
    *,
    settings: Settings,
    topic_id: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Probe an unapproved source over the network and persist an admission report.

    The source is never granted rights or enabled: the report records ``rights_approved=False``
    and a reminder that human T0 review is still required.
    """
    policy = fetch_policy_from(settings)
    feed_url = _configured_feed_url(config, topic_id)
    allowed_hosts = {
        _host(config.homepage_url),
        _host(config.feed_url),
        _host(config.extractor_hints.default_feed_url),
        *config.content_hosts,
    }
    if feed_url:
        allowed_hosts.add(_host(feed_url))
    allowed_hosts.discard("")
    homepage_host = _host(config.homepage_url)
    robots_url = f"https://{homepage_host}/robots.txt" if homepage_host else ""

    errors: list[str] = []
    network_calls = 0
    robots_text: str | None = None
    feed_raw: bytes | None = None
    feed_text: str | None = None
    sample_html: str | None = None
    sample_url = ""

    async def run() -> None:
        nonlocal network_calls, robots_text, feed_raw, feed_text, sample_html, sample_url
        client_kwargs = policy.client_kwargs()
        if transport is not None:
            client_kwargs["transport"] = transport

        async def get(url: str) -> bytes | None:
            nonlocal network_calls
            parsed = urlparse(url)
            if parsed.scheme != "https":
                errors.append(f"{url}: admission probe requires https")
                return None
            if _host(url) not in allowed_hosts:
                errors.append(f"{url}: host is not registered for source {config.source_id}")
                return None
            try:
                pinned_ip = await assert_url_permitted_async(
                    url,
                    config.to_registry_row(),
                    extra_hosts=frozenset(allowed_hosts),
                    check_dns=True,
                )
            except ValueError as exc:  # rejected before any request leaves the process
                errors.append(f"{url}: {exc}")
                return None
            network_calls += 1
            stream_url = url
            stream_kwargs: dict[str, Any] = {}
            if transport is None and pinned_ip:
                # Connect to the validated IP but keep TLS SNI and Host on the original name.
                stream_kwargs = {
                    "headers": {"Host": parsed.netloc},
                    "extensions": {"sni_hostname": parsed.hostname or ""},
                }
                stream_url = _url_with_host(url, pinned_ip)
            try:
                async with client.stream(
                    "GET", stream_url, follow_redirects=False, **stream_kwargs
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise ValueError(
                            f"unexpected redirect (HTTP {response.status_code}) from {url}"
                        )
                    response.raise_for_status()
                    declared = response.headers.get("content-length")
                    if declared and int(declared) > policy.max_bytes:
                        raise ValueError(f"response too large: {declared}")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > policy.max_bytes:
                            raise ValueError(f"response too large: {total} > {policy.max_bytes}")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except Exception as exc:  # one failed document must not abort the probe
                errors.append(f"{url}: {exc}")
                return None

        async with httpx.AsyncClient(**client_kwargs) as client:
            if robots_url:
                raw = await get(robots_url)
                if raw is None:
                    errors.append(
                        f"{robots_url}: robots.txt could not be fetched; skipping sample article"
                    )
                else:
                    robots_text = raw.decode("utf-8", errors="replace")
            # The probe checks route reachability even when robots disallows a path;
            # robots only gates the sample article below.
            if feed_url:
                feed_raw = await get(feed_url)

            feed_text = feed_raw.decode("utf-8", errors="replace") if feed_raw is not None else None
            probe_source_admission(
                config.homepage_url,
                feed_url=feed_url,
                robots_txt_content=robots_text,
                feed_sample_content=feed_text,
                archive_start_date=config.archive_start_date,
            )

            candidate = ""
            if feed_text is not None:
                try:
                    if "<urlset" in feed_text.lower() or "<sitemapindex" in feed_text.lower():
                        entries = parse_sitemap_xml(feed_text)
                    else:
                        entries = parse_rss_feed(feed_text)
                except Exception as exc:
                    errors.append(f"feed parse failed: {exc}")
                    entries = []
                for entry in entries:
                    url = urljoin(feed_url, entry.get("url", ""))
                    if urlparse(url).scheme == "https" and _host(url) in allowed_hosts:
                        candidate = url
                        break

            if candidate and robots_text is not None and robots_allows(robots_text, candidate):
                raw = await get(candidate)
                if raw is not None:
                    sample_url = candidate
                    sample_html = raw.decode("utf-8", errors="replace")

    asyncio.run(run())

    probe = probe_source_admission(
        config.homepage_url,
        feed_url=feed_url,
        robots_txt_content=robots_text,
        feed_sample_content=feed_text,
        sample_article_html=sample_html,
        sample_article_url=sample_url,
        archive_start_date=config.archive_start_date,
    )
    if robots_text is None:
        # A robots.txt that could not be fetched is never an allow-all: fail closed.
        probe["robots_txt_ok"] = False
        probe["admission_ready"] = False
    if feed_url and feed_text is None:
        # A configured feed that could not be fetched is never a depth pass.
        probe["feed_depth_ok"] = False
        probe["admission_ready"] = False
    report_path = (
        settings.audit_dir
        / "admission"
        / f"{config.source_id}-{settings.report_date.isoformat()}.json"
    )
    report: dict[str, Any] = {
        "source_id": config.source_id,
        "checked_at": datetime.now(UTC).isoformat(),
        "network_calls": network_calls,
        "sample_url": sample_url,
        "rights_approved": False,
        "notes": (
            "Admission probe only; no rights are granted. "
            "Human T0 rights review is still required before this source can be enabled."
        ),
        "errors": errors,
        "report_path": str(report_path),
        **probe,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        report_path, json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )
    return report
