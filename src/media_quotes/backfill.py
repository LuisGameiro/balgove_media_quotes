"""Shared historical backfill planning and execution for CLI and Dagster."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from .archive import ArchiveResult
from .config import Settings
from .db.resources import SessionContextFactory
from .exceptions import ConfigError
from .export import local_store_path
from .pipeline import DailyPipelineResult
from .risk_controls import budget_gate, estimate_backfill
from .risk_controls.models import BackfillEstimate


@dataclass(frozen=True, slots=True)
class BackfillRequest:
    """Validated operator inputs shared by CLI and Dagster."""

    start: date
    end: date
    max_cost: float | None = None
    execute: bool = False
    dry_run: bool = False
    topic_id: str | None = None
    source_mode: str = "fixtures"
    fetch_archive: bool = False
    max_archive_articles: int = 200
    pipeline: str = "mq-backfill"


@dataclass(frozen=True, slots=True)
class BackfillPlan:
    """Estimate and safety-gate result before any archive or partition work."""

    estimate: BackfillEstimate
    cached_hashes: int
    cache_rate: float
    budget_note: str
    span_days: int
    effective_end: date
    sources: int


@dataclass(frozen=True, slots=True)
class PartitionSummary:
    """Compact result for one daily partition."""

    report_date: date
    quotes: int
    gaps: int
    rejected: int
    status: str
    persisted_quotes: int


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """Structured result used by CLI formatting and Dagster metadata."""

    plan: BackfillPlan
    archives: list[ArchiveResult] = field(default_factory=list)
    partitions: list[PartitionSummary] = field(default_factory=list)
    total_quotes: int = 0
    total_gaps: int = 0
    total_rejected: int = 0
    persisted_quotes: int = 0
    estimate_run_id: str | None = None

    @property
    def archive_errors(self) -> int:
        return sum(len(result.errors) for result in self.archives)

    @property
    def archive_stored_articles(self) -> int:
        return sum(result.stored_articles for result in self.archives)


def _load_rows(settings: Settings) -> list[Any]:
    from .cli.common import _load_registry

    return [row for row in _load_registry(settings) if row.tier < 4]


def _cached_hashes(settings: Settings, request: BackfillRequest, span_days: int) -> int:
    cache_mode = "blobs" if request.fetch_archive else request.source_mode
    cache_settings = settings.for_source_mode(cache_mode)
    cached = 0
    for offset in range(span_days):
        current = request.start + timedelta(days=offset)
        path = local_store_path(cache_settings.export_dir, current, topic_id=request.topic_id)
        if not path.is_file():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and isinstance(payload.get("content_hash"), str):
                    cached += 1
    return cached


def plan_backfill(settings: Settings, request: BackfillRequest) -> BackfillPlan:
    """Validate inputs and calculate the existing bounded cost/cache estimate."""
    from .cli.common import (
        _ARTICLES_PER_HOUR,
        _ARTICLES_PER_SOURCE_DAY,
        _CALLS_PER_ARTICLE,
        _MAX_BACKFILL_DAYS,
        _PRICE_PER_CALL,
    )

    if request.source_mode not in {"fixtures", "blobs", "live"}:
        raise ConfigError("source_mode must be fixtures, blobs or live")
    if request.fetch_archive and request.max_archive_articles <= 0:
        raise ConfigError("--max-archive-articles must be positive")
    if request.end < request.start:
        raise ConfigError("--to must not precede --from")
    if request.execute and request.max_cost is None:
        raise ConfigError("--max-cost is required to execute a backfill")

    span = min((request.end - request.start).days + 1, _MAX_BACKFILL_DAYS)
    rows = _load_rows(settings)
    base = estimate_backfill(
        articles_per_source_day=_ARTICLES_PER_SOURCE_DAY,
        sources=max(1, len(rows)),
        days=span,
        calls_per_article=_CALLS_PER_ARTICLE,
        price_per_call=_PRICE_PER_CALL,
        articles_per_hour=_ARTICLES_PER_HOUR,
    )
    cached_hashes = _cached_hashes(settings, request, span)
    cache_rate = min(0.9, cached_hashes / max(1, base.calls))
    estimate = estimate_backfill(
        articles_per_source_day=_ARTICLES_PER_SOURCE_DAY,
        sources=max(1, len(rows)),
        days=span,
        calls_per_article=_CALLS_PER_ARTICLE,
        price_per_call=_PRICE_PER_CALL,
        articles_per_hour=_ARTICLES_PER_HOUR,
        expected_cache_hit_rate=cache_rate,
    )
    note = (
        "budget not capped" if request.max_cost is None else budget_gate(estimate, request.max_cost)
    )
    return BackfillPlan(
        estimate=estimate,
        cached_hashes=cached_hashes,
        cache_rate=cache_rate,
        budget_note=note,
        span_days=span,
        effective_end=request.start + timedelta(days=span - 1),
        sources=len(rows),
    )


def _record_estimate(
    settings: Settings,
    request: BackfillRequest,
    plan: BackfillPlan,
    session_context: SessionContextFactory | None,
) -> str | None:
    if not settings.persist_to_postgres:
        return None
    from .db.repository import finish_pipeline_run, start_pipeline_run
    from .pipeline import _default_session_context

    prefix = "backfill" if request.pipeline == "mq-backfill" else f"{request.pipeline}-estimate"
    run_id = f"{prefix}-{request.start.isoformat()}-{request.end.isoformat()}"
    stats = {
        **asdict(plan.estimate),
        "max_cost": request.max_cost,
        "budget_note": plan.budget_note,
        "span_days": plan.span_days,
        "cached_hashes": plan.cached_hashes,
        "fetch_archive": request.fetch_archive,
        "max_archive_articles": request.max_archive_articles,
        "source_mode": request.source_mode,
    }
    factory = session_context or _default_session_context
    with factory(settings) as session:
        start_pipeline_run(
            session,
            run_id=run_id,
            pipeline=request.pipeline,
            partition_date=request.start,
            commit=False,
        )
        finish_pipeline_run(session, run_id=run_id, status="succeeded", stats=stats, commit=False)
        session.commit()
    return run_id


def _discover_archive(
    settings: Settings, request: BackfillRequest, plan: BackfillPlan
) -> list[ArchiveResult]:
    from .archive import discover_archive_documents
    from .cli.common import _load_source_configs, _load_topic_pack
    from .cli.fetch import _resolve_feed_url
    from .fetch import fetch_policy_from

    rows = _load_rows(settings)
    configs = _load_source_configs(settings)
    policy = fetch_policy_from(settings).model_copy(update={"follow_redirects": True})
    topic_pack = _load_topic_pack(settings, request.topic_id)
    remaining = request.max_archive_articles
    results: list[ArchiveResult] = []
    for row in rows:
        if remaining <= 0:
            break
        if not row.usable or row.tier_block or row.discovery_method not in {"rss", "sitemap"}:
            continue
        feed_url = ""
        try:
            config = configs.get(row.source_id)
            feed_url = (
                config.discovery_feed_url(request.topic_id or settings.active_topic)
                if config
                else ""
            )
            feed_url = feed_url or row.feed_url
            feed_url = feed_url or _resolve_feed_url(
                settings, row.source_id, request.topic_id or settings.active_topic
            )
            if not feed_url:
                continue
            result = discover_archive_documents(
                row,
                feed_url,
                policy=policy,
                topic_pack=topic_pack,
                start=request.start,
                end=plan.effective_end,
                max_articles=remaining,
                require_topic=True,
                extra_hosts=frozenset(config.redirect_hosts) if config else frozenset(),
            )
        except Exception as exc:  # one bad source must not abort the range
            result = ArchiveResult(
                source_id=row.source_id, feed_url=feed_url, errors=[f"{row.source_id}: {exc}"]
            )
        remaining -= result.discovered_urls
        results.append(result)
    return results


def _partition_summary(result: DailyPipelineResult) -> PartitionSummary:
    return PartitionSummary(
        report_date=result.report_date,
        quotes=len(result.records),
        gaps=len(result.gaps),
        rejected=result.rejected_total,
        status=result.status,
        persisted_quotes=int(result.persistence.get("stored_quotes", 0)),
    )


def execute_backfill(
    settings: Settings,
    request: BackfillRequest,
    *,
    plan: BackfillPlan | None = None,
    session_context: SessionContextFactory | None = None,
) -> BackfillResult:
    """Execute the shared archive/partition plan, or persist an estimate-only result."""
    from .pipeline import run_daily_pipeline

    active_plan = plan or plan_backfill(settings, request)
    if not request.execute or request.dry_run:
        estimate_run_id = _record_estimate(settings, request, active_plan, session_context)
        return BackfillResult(plan=active_plan, estimate_run_id=estimate_run_id)

    source_mode = "blobs" if request.fetch_archive else request.source_mode
    archives = _discover_archive(settings, request, active_plan) if request.fetch_archive else []
    partitions: list[PartitionSummary] = []
    for offset in range(active_plan.span_days):
        current = request.start + timedelta(days=offset)
        result = run_daily_pipeline(
            settings,
            report_date=current,
            dry_run=False,
            pipeline=request.pipeline,
            topic_id=request.topic_id,
            source_mode=source_mode,
            session_context=session_context,
        )
        partitions.append(_partition_summary(result))

    return BackfillResult(
        plan=active_plan,
        archives=archives,
        partitions=partitions,
        total_quotes=sum(item.quotes for item in partitions),
        total_gaps=sum(item.gaps for item in partitions),
        total_rejected=sum(item.rejected for item in partitions),
        persisted_quotes=sum(item.persisted_quotes for item in partitions),
    )
