"""C4 — Dagster daily-partitioned orchestration wrapper (DEC-04).

The asset delegates all domain work to the existing offline CLI pipeline (`_collect`,
`write_digest`) so behaviour is identical whether invoked via `mq run` or Dagster. Postgres
persistence is opt-in via `MQ_PERSIST_TO_POSTGRES=true`; by default the asset runs fully
offline against fixtures, which keeps it safe while every registry row is T0-blocked.
Only when a session is available does it write `pipeline_runs`, quotes and append-only audit events.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

import dagster as dg

from .config import Settings, get_settings

PARTITION_START = "2026-10-01"
CODE_VERSION = "c4-daily-pipeline-v1"


def build_definitions() -> dg.Definitions:
    """Build Dagster definitions lazily so importing this module never starts a daemon."""

    @dg.asset(
        partitions_def=dg.DailyPartitionsDefinition(start_date=PARTITION_START),
        code_version=CODE_VERSION,
        name="daily_quotes_digest",
    )
    def daily_quotes_digest(context) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        settings: Settings = get_settings()
        report_date: date = date.fromisoformat(context.partition_key)
        # Imported inside the asset to keep CLI-helper tests independent of Dagster.
        from .cli.common import _NOTICE, _window
        from .cli.run import _collect, _load_review_flags
        from .curation import dedupe_quotes, review_badge
        from .digest import write_digest
        from .export import local_store_path, write_jsonl
        from .llm import make_provider
        from .rights_guard import apply_rights_limits

        records, coverage, gaps, events, rejected, offsets = _collect(
            settings, run_id=f"run-dagster-{report_date.isoformat()}", report_date=report_date
        )
        rights = apply_rights_limits(records, max_quote_words=settings.max_quote_words)
        records = dedupe_quotes(rights.kept, window_days=settings.report_window_days)
        store_path = local_store_path(settings.export_dir, report_date)
        write_jsonl(records, store_path)

        review_flags = _load_review_flags(settings)
        now = datetime.combine(report_date, time(12, 0), tzinfo=UTC)
        review_badges = {
            flag.evidence.old_quote_id: review_badge(flag, now=now)
            for flag in review_flags
            if review_badge(flag, now=now)
        }

        digest_path = write_digest(
            records,
            report_date=report_date,
            window_start=_window(report_date, settings)[0],
            window_end=report_date,
            out_path=settings.digest_dir / f"{report_date.isoformat()}.md",
            sections_csv=settings.sections_csv,
            provider=make_provider("local"),
            obsolescence_flags=review_flags,
            review_badges=review_badges,
            notice=_NOTICE,
        )

        status = "partial" if gaps else "succeeded"
        summary = {
            "report_date": report_date.isoformat(),
            "quotes": len(records),
            "coverage": len(coverage),
            "gaps": len(gaps),
            "rejected": rejected + len(rights.rejected),
            "digest_path": str(digest_path),
            "status": status,
        }
        if settings.persist_to_postgres:
            from .db.engine import create_db_engine, make_session_factory
            from .db.repository import (
                append_audit_events,
                finish_pipeline_run,
                persist_articles_and_quotes,
                start_pipeline_run,
            )

            engine = create_db_engine(settings)
            run_id = f"dagster-{report_date.isoformat()}"
            with make_session_factory(engine)() as session:
                start_pipeline_run(
                    session, run_id=run_id, pipeline="dagster-daily", partition_date=report_date
                )
                articles_n, quotes_n = persist_articles_and_quotes(session, records, offsets)
                append_audit_events(session, events + coverage)
                finish_pipeline_run(
                    session,
                    run_id=run_id,
                    status=status,
                    stats={
                        **summary,
                        "articles": articles_n,
                        "stored_quotes": quotes_n,
                    },
                )
        context.add_output_metadata(summary)
        return summary

    return dg.Definitions(assets=[daily_quotes_digest])
