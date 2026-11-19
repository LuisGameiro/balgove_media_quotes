"""C4 — Dagster daily-partitioned orchestration wrapper (DEC-04).

The asset delegates all domain work to the existing offline CLI pipeline (`_collect`,
`write_digest`) so behaviour is identical whether invoked via `mq run` or Dagster. Postgres
persistence is opt-in via `MQ_PERSIST_TO_POSTGRES=true`; by default the asset runs fully
offline against fixtures, which keeps it safe while every registry row is T0-blocked.
Only when a session is available does it write `pipeline_runs` and append-only audit events.
"""

from __future__ import annotations

from datetime import date
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
    def daily_quotes_digest(context) -> dict[str, Any]:
        settings: Settings = get_settings()
        report_date: date = date.fromisoformat(context.partition_key)
        # Imported inside the asset to keep CLI-helper tests independent of Dagster.
        from .cli.run import _collect

        records, coverage, gaps, events, rejected = _collect(
            settings, run_id=f"run-dagster-{report_date.isoformat()}", report_date=report_date
        )
        status = "partial" if gaps else "succeeded"
        summary = {
            "report_date": report_date.isoformat(),
            "quotes": len(records),
            "coverage": len(coverage),
            "gaps": len(gaps),
            "rejected": rejected,
            "status": status,
        }
        if settings.persist_to_postgres:
            from .db.engine import create_db_engine, make_session_factory
            from .db.repository import append_audit_events, finish_pipeline_run, start_pipeline_run

            engine = create_db_engine(settings)
            run_id = f"dagster-{report_date.isoformat()}"
            with make_session_factory(engine)() as session:
                start_pipeline_run(
                    session, run_id=run_id, pipeline="dagster-daily", partition_date=report_date
                )
                append_audit_events(session, events + coverage)
                finish_pipeline_run(session, run_id=run_id, status=status, stats=summary)
        context.add_output_metadata(summary)
        return summary

    return dg.Definitions(assets=[daily_quotes_digest])
