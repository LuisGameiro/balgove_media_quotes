"""Explicit live-fetch command for rights-cleared sources.

Dry-run by default at the parser level; every live call still passes the T0 gate and the
fetch host allowlist. This command deliberately writes no database rows — persistence stays
with ``mq run``.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from typing import Any

from ..adapters import build_adapter
from ..config import DEFAULT_TOPIC_ID, Settings
from ..fetch import fetch_policy_from
from ..live import fetch_feed_documents
from ..topics import TopicPack
from .common import EXIT_CONFIG, EXIT_OK, _load_registry, _load_source_configs, _load_topic_pack


def _resolve_feed_url(settings: Settings, source_id: str, topic_id: str) -> str:
    """Prefer the topic route's feed URL from the source config, then the registry feed."""
    config = _load_source_configs(settings).get(source_id)
    if config is not None:
        url = config.discovery_feed_url(topic_id)
        if url:
            return url
    row = next((row for row in _load_registry(settings) if row.source_id == source_id), None)
    return row.feed_url if row is not None else ""


def _do_fetch(
    settings: Settings,
    *,
    source_id: str,
    report_date: date,
    limit: int = 2,
    dry_run: bool = True,
    topic_id: str | None = None,
) -> int:
    """Fetch a source's topic feed and the first N articles into the local blob cache."""
    row = next((row for row in _load_registry(settings) if row.source_id == source_id), None)
    if row is None:
        print(f"error: unknown source_id {source_id!r}", file=sys.stderr)
        return EXIT_CONFIG

    if not row.usable:
        print(
            f"error: source {source_id!r} is blocked by the T0 rights gate "
            f"(rights={row.rights_status}, access={row.access_mode})",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    pack: TopicPack | None = _load_topic_pack(settings, topic_id)
    active_topic = (
        pack.topic_id
        if pack is not None
        else str(topic_id or getattr(settings, "active_topic", DEFAULT_TOPIC_ID))
    )
    feed_url = _resolve_feed_url(settings, source_id, active_topic)
    if not feed_url:
        print(
            f"error: no feed URL registered for {source_id!r}; pass one in the source config",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    if dry_run:
        print(
            json.dumps(
                {
                    "source_id": source_id,
                    "topic_id": active_topic,
                    "report_date": report_date.isoformat(),
                    "feed_url": feed_url,
                    "t0_gate_permitted": True,
                    "dry_run": True,
                    "articles_to_fetch": max(1, limit),
                    "network_calls": 0,
                },
                indent=2,
                ensure_ascii=True,
            )
        )
        return EXIT_OK

    policy = fetch_policy_from(settings)
    source_cfg = _load_source_configs(settings).get(source_id)
    extra_hosts = frozenset(
        [*source_cfg.content_hosts, *source_cfg.redirect_hosts] if source_cfg else []
    )
    discovered, downloaded = fetch_feed_documents(
        row,
        feed_url,
        policy=policy,
        topic_pack=pack,
        require_topic=True,
        limit=limit,
        extra_hosts=extra_hosts,
    )

    adapter = build_adapter(
        row,
        replay=False,
        topic_pack=pack,
        extraction_fallback=settings.extraction_fallback,
        source_config=source_cfg,
    )
    articles: list[dict[str, Any]] = []
    for article in downloaded:
        if article.error:
            articles.append({"url": article.url, "error": article.error})
            continue
        metadata = adapter.parse_metadata(article.raw, source_url=article.url)
        spans = adapter.extract_quotes(str(metadata.get("article_text") or ""))
        articles.append(
            {
                "url": article.url,
                "title": article.title,
                "sha256": article.sha256,
                "blob": article.blob_path,
                "quotes": len(spans),
            }
        )

    report = {
        "source_id": source_id,
        "topic_id": active_topic,
        "report_date": report_date.isoformat(),
        "feed_url": feed_url,
        "t0_gate_permitted": True,
        "dry_run": False,
        "articles_discovered": len(discovered),
        "articles_fetched": len(articles),
        "articles": articles,
    }
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return EXIT_OK
