"""Pass 3 — bounded historical backfill over rights-gated archive discovery.

Discovery and download use the same host allowlist as live collection. Paid inference
stays off: a backfill refuses to run unless the local/offline workstreams are selected.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from .adapters import BlobCache, FixtureSource, build_adapter
from .archive import discover_historical_articles
from .cli.common import (
    _MAX_BACKFILL_DAYS,
    _load_registry,
    _load_source_configs,
    _load_topic_pack,
)
from .config import DEFAULT_TOPIC_ID, Settings
from .exceptions import ConfigError
from .export import atomic_write_text
from .fetch import fetch_policy_from
from .live import fetch_url_documents
from .pipeline import run_daily_pipeline

_DONE_STATUSES = {"succeeded", "partial"}


def _manifest_path(settings: Settings, start: date, end: date, topic_id: str) -> Path:
    safe_topic = re.sub(r"[^a-z0-9_-]+", "-", topic_id.strip().lower()).strip("-") or "topic"
    return (
        settings.audit_dir
        / "backfills"
        / f"backfill-{start.isoformat()}-{end.isoformat()}-{safe_topic}.json"
    )


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"corrupt backfill manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"corrupt backfill manifest {path}: expected a JSON object")
    return payload


def _save_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    atomic_write_text(
        path, json.dumps(dict(manifest), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


def _summarise(
    manifest_path: Path,
    manifest: Mapping[str, Any],
    days: list[date],
    *,
    discovered: int,
    fetched: int,
    max_cost: float,
) -> dict[str, Any]:
    completed = dict(manifest.get("completed", {}))
    statuses = [str(completed.get(day.isoformat(), {}).get("status", "pending")) for day in days]
    return {
        "manifest": str(manifest_path),
        "run_id": manifest.get("run_id", ""),
        "start": manifest.get("start", days[0].isoformat() if days else ""),
        "end": manifest.get("end", days[-1].isoformat() if days else ""),
        "topic_id": manifest.get("topic_id", ""),
        "days": len(days),
        "quotes": sum(int(state.get("quotes", 0)) for state in completed.values()),
        "gaps": sum(int(state.get("gaps", 0)) for state in completed.values()),
        "statuses": {status: statuses.count(status) for status in sorted(set(statuses))},
        "discovered": discovered,
        "fetched": fetched,
        "issues": list(manifest.get("issues", [])),
        "max_cost": max_cost,
        "paid_calls": 0,
        "completed": completed,
    }


def run_historical_backfill(
    settings: Settings,
    *,
    start: date,
    end: date,
    max_cost: float,
    topic_id: str | None = None,
    source_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Discover, fetch and run the archive pipeline for every day in ``start..end``."""
    if end < start:
        raise ConfigError("--to must not precede --from")
    if (end - start).days + 1 > _MAX_BACKFILL_DAYS:
        raise ConfigError(f"backfill span exceeds the {_MAX_BACKFILL_DAYS}-day limit")
    if max_cost is None or max_cost <= 0:
        raise ConfigError("--max-cost must be present and positive to execute a backfill")
    llm_provider = str(settings.llm_provider).strip().lower()
    translation_mode = str(settings.translation_mode).strip().lower()
    ai_judge_mode = str(settings.ai_judge_mode).strip().lower()
    if llm_provider != "local" or translation_mode != "off" or ai_judge_mode != "off":
        raise ConfigError(
            "historical backfill requires LLM_PROVIDER=local, TRANSLATION_MODE=off "
            "and AI_JUDGE_MODE=off; paid historical inference is not implemented"
        )

    active_topic_id = str(
        topic_id or getattr(settings, "active_topic", DEFAULT_TOPIC_ID) or DEFAULT_TOPIC_ID
    )
    manifest_path = _manifest_path(settings, start, end, active_topic_id)
    manifest = _load_manifest(manifest_path)
    manifest.setdefault(
        "run_id", f"backfill-{start.isoformat()}-{end.isoformat()}-{active_topic_id}"
    )
    manifest.update(
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "topic_id": active_topic_id,
        }
    )
    completed: dict[str, Any] = manifest.setdefault("completed", {})
    issues: list[str] = manifest.setdefault("issues", [])
    days = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    pending = [
        day
        for day in days
        if completed.get(day.isoformat(), {}).get("status") not in _DONE_STATUSES
    ]
    if not pending:
        # Full resume: no discovery, no fetch, no pipeline calls.
        return _summarise(manifest_path, manifest, days, discovered=0, fetched=0, max_cost=max_cost)
    pending_dates = {day.isoformat() for day in pending}
    discover_start, discover_end = min(pending), max(pending)

    policy = fetch_policy_from(settings)
    topic_pack = _load_topic_pack(settings, active_topic_id, strict=topic_id is not None)
    configs = _load_source_configs(settings)
    cache = BlobCache(settings.blob_dir)
    refs_by_date: dict[str, dict[str, list[str]]] = {}
    processed_source_ids: set[str] = set()
    discovered = 0
    fetched = 0

    for row in _load_registry(settings):
        if source_ids is not None and row.source_id not in source_ids:
            continue
        if not row.usable:
            continue  # T0 gate: an unusable source is never contacted.
        try:
            config = configs.get(row.source_id)
            if config is None:
                issues.append(f"{row.source_id}: no source config for archive discovery")
                continue
            processed_source_ids.add(row.source_id)
            result = discover_historical_articles(
                row,
                config,
                start=discover_start,
                end=discover_end,
                topic_id=active_topic_id,
                policy=policy,
                limit=settings.archive_article_limit,
            )
            issues.extend(f"{row.source_id}: {error}" for error in result.errors)
            discovered += len(result.candidates)
            items = [
                {"url": article.url, "title": article.title, "published": article.published}
                for article in result.candidates
            ]
            if not items:
                continue
            articles = fetch_url_documents(
                row,
                items,
                policy=policy,
                cache=cache,
                extra_hosts=frozenset([*config.content_hosts, *config.redirect_hosts]),
                provenance="live_feed",
                discovered_via="archive",
            )
            adapter = build_adapter(
                row,
                FixtureSource(settings.blob_dir),
                topic_pack=topic_pack,
                source_config=config,
                extraction_fallback=settings.extraction_fallback,
            )
            for article in articles:
                if article.error or not article.blob_path:
                    issues.append(
                        f"{row.source_id}: fetch failed for {article.url}: "
                        f"{article.error or 'no blob path'}"
                    )
                    continue
                raw = article.raw or Path(article.blob_path).read_bytes()
                metadata = adapter.parse_metadata(raw, source_url=article.url)
                published = str(metadata.get("publication_date") or "")[:10]
                try:
                    day = date.fromisoformat(published)
                except ValueError:
                    issues.append(f"{row.source_id}: undated article {article.url}")
                    continue
                if day.isoformat() not in pending_dates:
                    issues.append(
                        f"{row.source_id}: out-of-window or completed article "
                        f"{article.url} published {day}"
                    )
                    continue
                ref = f"fixture://{row.source_id}/{Path(article.blob_path).name}"
                refs_by_date.setdefault(day.isoformat(), {}).setdefault(row.source_id, []).append(
                    ref
                )
                fetched += 1
        except Exception as exc:
            issues.append(f"{row.source_id}: archive discovery/fetch failed: {exc}")
            continue

    for day in days:
        key = day.isoformat()
        if completed.get(key, {}).get("status") in _DONE_STATUSES:
            continue
        outcome = run_daily_pipeline(
            settings,
            report_date=day,
            pipeline="mq-backfill-archive",
            topic_id=active_topic_id,
            source_mode="archive",
            provenance="live_feed",
            prefetched_refs=refs_by_date.get(key, {}),
            source_ids=processed_source_ids,
        )
        completed[key] = {
            "status": outcome.status,
            "quotes": len(outcome.records),
            "gaps": len(outcome.gaps),
            "run_id": outcome.run_id,
        }
        manifest["updated_at"] = datetime.now(UTC).isoformat()
        _save_manifest(manifest_path, manifest)

    return _summarise(
        manifest_path, manifest, days, discovered=discovered, fetched=fetched, max_cost=max_cost
    )
