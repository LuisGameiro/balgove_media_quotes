"""Public daily pipeline service shared by the CLI and Dagster.

The service owns the fixture/T0-gated collection, deterministic verification, dedupe,
file artifacts, audit report and optional Postgres persistence. Callers supply only the
settings, date and their own session context when they want dependency injection.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any

from .config import Settings
from .db.resources import SessionContextFactory
from .models import ExportQuoteRecord
from .risk_controls.models import ObsolescenceFlag


@dataclass(frozen=True, slots=True)
class DailyPipelineResult:
    """Auditable output of one daily collection run."""

    run_id: str
    report_date: date
    dry_run: bool
    records: tuple[ExportQuoteRecord, ...]
    coverage: tuple[dict[str, Any], ...]
    gaps: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]
    rejected: int
    rejected_total: int
    status: str
    review_flags: tuple[ObsolescenceFlag, ...] = ()
    offsets: dict[str, dict[str, Any]] = field(default_factory=dict)
    store_path: Path | None = None
    digest_path: Path | None = None
    logs_path: Path | None = None
    audit_json_path: Path | None = None
    audit_md_path: Path | None = None
    checks: dict[str, Any] = field(default_factory=dict)
    persistence: dict[str, Any] = field(default_factory=dict)


@contextmanager
def _default_session_context(settings: Settings) -> Any:
    """Create and dispose an engine for a caller-owned session."""
    from .db.engine import create_db_engine, make_session_factory

    engine = create_db_engine(settings)
    try:
        with make_session_factory(engine)() as session:
            yield session
    finally:
        engine.dispose()


def _persist_result(
    result: DailyPipelineResult,
    settings: Settings,
    *,
    pipeline: str,
    session_context: SessionContextFactory | None,
) -> dict[str, Any]:
    from .cli.common import _window
    from .db.repository import (
        append_audit_events,
        finish_pipeline_run,
        persist_articles_and_quotes,
        persist_digest_manifest,
        persist_obsolescence_flags,
        persist_quote_classifications,
        start_pipeline_run,
    )

    window_start, _ = _window(result.report_date, settings)
    assigned_by = "llm" if settings.llm_provider == "openrouter" else "rule"
    context_factory = session_context or _default_session_context
    with context_factory(settings) as session:
        start_pipeline_run(
            session,
            run_id=result.run_id,
            pipeline=pipeline,
            partition_date=result.report_date,
        )
        articles_n, quotes_n = persist_articles_and_quotes(
            session, list(result.records), result.offsets
        )
        classifications_n = persist_quote_classifications(
            session, list(result.records), result.offsets, assigned_by=assigned_by
        )
        digest_id = persist_digest_manifest(
            session,
            digest_date=result.report_date,
            window_start=window_start,
            window_end=result.report_date,
            md_path=str(result.digest_path or ""),
            records=list(result.records),
        )
        flags_n = persist_obsolescence_flags(session, list(result.review_flags))
        append_audit_events(session, list(result.events) + list(result.coverage))
        finish_pipeline_run(
            session,
            run_id=result.run_id,
            status=result.status,
            stats={
                **result.checks,
                "quotes": len(result.records),
                "articles": articles_n,
                "stored_quotes": quotes_n,
                "classifications": classifications_n,
                "obsolescence_flags": flags_n,
                "digest_id": digest_id,
            },
        )
    return {
        "articles": articles_n,
        "stored_quotes": quotes_n,
        "classifications": classifications_n,
        "obsolescence_flags": flags_n,
        "digest_id": digest_id,
    }


def run_daily_pipeline(
    settings: Settings,
    *,
    report_date: date,
    run_id: str | None = None,
    dry_run: bool = False,
    pipeline: str = "mq-run",
    persist_to_postgres: bool | None = None,
    session_context: SessionContextFactory | None = None,
) -> DailyPipelineResult:
    """Run one report date through the shared T0-gated offline pipeline."""
    from .cli.audit import _audit_report, _write_audit
    from .cli.common import (
        _NOTICE,
        _run_id,
        _window,
        _write_json,
    )
    from .cli.run import _collect, _load_review_flags
    from .curation import dedupe_quotes, review_badge
    from .digest import write_digest
    from .export import local_store_path, write_jsonl
    from .llm import make_provider
    from .rights_guard import apply_rights_limits

    actual_run_id = run_id or _run_id(report_date, settings)
    records, coverage, gaps, events, rejected, offsets = _collect(
        settings, run_id=actual_run_id, report_date=report_date
    )

    if dry_run:
        return DailyPipelineResult(
            run_id=actual_run_id,
            report_date=report_date,
            dry_run=True,
            records=tuple(records),
            coverage=tuple(coverage),
            gaps=tuple(gaps),
            events=tuple(events),
            rejected=rejected,
            rejected_total=rejected,
            status="dry-run",
            offsets=offsets,
        )

    rights = apply_rights_limits(records, max_quote_words=settings.max_quote_words)
    kept = dedupe_quotes(rights.kept, window_days=settings.report_window_days)
    store_path = local_store_path(settings.export_dir, report_date)
    write_jsonl(kept, store_path)

    review_flags = _load_review_flags(settings)
    now = datetime.combine(report_date, time(12, 0), tzinfo=UTC)
    review_badges = {
        flag.evidence.old_quote_id: review_badge(flag, now=now)
        for flag in review_flags
        if review_badge(flag, now=now)
    }

    digest_path = write_digest(
        kept,
        report_date=report_date,
        window_start=_window(report_date, settings)[0],
        window_end=report_date,
        out_path=settings.digest_dir / f"{report_date.isoformat()}.md",
        sections_csv=settings.sections_csv,
        provider=make_provider(settings),
        obsolescence_flags=review_flags,
        review_badges=review_badges,
        notice=_NOTICE,
    )

    logs_path = settings.logs_dir / f"{actual_run_id}.jsonl"
    logs_path.parent.mkdir(parents=True, exist_ok=True)
    with logs_path.open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(
                json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
            handle.write("\n")

    report = _audit_report(
        settings,
        report_date=report_date,
        run_id=actual_run_id,
        records=kept,
        coverage=coverage,
        gaps=gaps,
        events=events,
        rejected=rejected + len(rights.rejected),
        review_queue=[flag.model_dump(mode="json") for flag in review_flags],
    )
    audit_json_path, audit_md_path = _write_audit(settings, report)
    _write_json(settings.audit_dir / f"{actual_run_id}.json", report)

    status = "partial" if gaps else "succeeded"
    result = DailyPipelineResult(
        run_id=actual_run_id,
        report_date=report_date,
        dry_run=False,
        records=tuple(kept),
        coverage=tuple(coverage),
        gaps=tuple(gaps),
        events=tuple(events),
        rejected=rejected,
        rejected_total=rejected + len(rights.rejected),
        status=status,
        review_flags=tuple(review_flags),
        offsets=offsets,
        store_path=store_path,
        digest_path=digest_path,
        logs_path=logs_path,
        audit_json_path=audit_json_path,
        audit_md_path=audit_md_path,
        checks=dict(report["checks"]),
    )

    should_persist = (
        settings.persist_to_postgres if persist_to_postgres is None else persist_to_postgres
    )
    if should_persist:
        result.persistence.update(
            _persist_result(result, settings, pipeline=pipeline, session_context=session_context)
        )
    return result
