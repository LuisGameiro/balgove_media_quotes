"""Convert audit_events to physical monthly range partitions (RSK-11, CHK-17).

All DDL for the conversion lives in this file so an operator has one unit to apply and
one to roll back. Fresh databases get a plain table from 0001 and convert here; existing
databases need a maintenance window:

  1. pg_dump the database (backup) and record the audit_events row count.
  2. Stop the Dagster schedule and any CLI writers; no audit inserts during the window.
  3. alembic upgrade head (this revision holds an ACCESS EXCLUSIVE lock on audit_events).
  4. Verify the row count and that pg_partitioned_table lists audit_events; check the
     partition registry with `mq retention`.
  5. Rollback: alembic downgrade -1 reverses the conversion. If that fails, restore the
     pg_dump from step 1.

The default partition catches rows for months without an explicit partition; attaching
one later requires moving those rows out of the default partition first.
"""

from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from alembic import op

revision = "0007_audit_events_monthly_partition"
down_revision = "0007_widen_version"
branch_labels = None
depends_on = None

_COLUMNS = """
    event_id    BIGINT NOT NULL DEFAULT nextval('audit_events_event_id_seq'),
    run_id      UUID REFERENCES pipeline_runs(run_id),
    trace_id    TEXT,
    step        TEXT NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    status      TEXT NOT NULL,
    details     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
"""
_COPY_UP_SQL = (
    "INSERT INTO audit_events (event_id, run_id, trace_id, step, entity_type, entity_id, "
    "status, details, created_at) SELECT event_id, run_id, trace_id, step, entity_type, "
    "entity_id, status, details, created_at FROM audit_events_legacy"
)
_COPY_DOWN_SQL = (
    "INSERT INTO audit_events_legacy (event_id, run_id, trace_id, step, entity_type, "
    "entity_id, status, details, created_at) SELECT event_id, run_id, trace_id, step, "
    "entity_type, entity_id, status, details, created_at FROM audit_events"
)


def _is_partitioned(connection: sa.Connection) -> bool:
    return (
        connection.execute(
            sa.text(
                "SELECT 1 FROM pg_class c "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "JOIN pg_partitioned_table p ON p.partrelid = c.oid "
                "WHERE c.relname = 'audit_events' AND n.nspname = current_schema()"
            )
        ).first()
        is not None
    )


def _observed_months(connection: sa.Connection) -> list[date]:
    """Only months with rows need an explicit partition; the rest use the default."""
    return list(
        connection.execute(
            sa.text(
                "SELECT DISTINCT date_trunc('month', created_at AT TIME ZONE 'UTC')::date "
                "FROM audit_events_legacy ORDER BY 1"
            )
        )
        .scalars()
        .all()
    )


def _free_legacy_names(connection: sa.Connection) -> None:
    """Sequences, indexes and constraints are schema-wide names, not per-table."""
    connection.execute(sa.text("ALTER SEQUENCE audit_events_event_id_seq OWNED BY NONE"))
    connection.execute(sa.text("DROP INDEX IF EXISTS audit_events_run_idx"))
    connection.execute(sa.text("DROP INDEX IF EXISTS audit_events_trace_idx"))
    connection.execute(
        sa.text("ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS audit_events_pkey")
    )
    connection.execute(
        sa.text("ALTER TABLE audit_events DROP CONSTRAINT IF EXISTS audit_events_run_id_fkey")
    )


def _next_month(month: date) -> date:
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def upgrade() -> None:
    connection = op.get_bind()
    if _is_partitioned(connection):
        return

    _free_legacy_names(connection)
    connection.execute(sa.text("ALTER TABLE audit_events RENAME TO audit_events_legacy"))
    connection.execute(
        sa.text(
            f"CREATE TABLE audit_events ({_COLUMNS}, PRIMARY KEY (event_id, created_at)) "
            "PARTITION BY RANGE (created_at)"
        )
    )
    for month in _observed_months(connection):
        connection.execute(
            sa.text(
                f"CREATE TABLE audit_events_{month:%Y_%m} PARTITION OF audit_events "
                f"FOR VALUES FROM (TIMESTAMPTZ '{month.isoformat()} 00:00:00+00') "
                f"TO (TIMESTAMPTZ '{_next_month(month).isoformat()} 00:00:00+00')"
            )
        )
    connection.execute(
        sa.text("CREATE TABLE audit_events_default PARTITION OF audit_events DEFAULT")
    )
    connection.execute(
        sa.text("CREATE INDEX audit_events_run_idx ON audit_events (run_id, created_at)")
    )
    connection.execute(sa.text("CREATE INDEX audit_events_trace_idx ON audit_events (trace_id)"))
    connection.execute(sa.text(_COPY_UP_SQL))
    before = connection.execute(sa.text("SELECT count(*) FROM audit_events_legacy")).scalar_one()
    after = connection.execute(sa.text("SELECT count(*) FROM audit_events")).scalar_one()
    if before != after:
        raise RuntimeError(f"audit_events partition copy lost rows: {before} -> {after}")
    connection.execute(
        sa.text(
            "SELECT setval('audit_events_event_id_seq', "
            "GREATEST(COALESCE((SELECT max(event_id) FROM audit_events), 1), 1))"
        )
    )
    connection.execute(
        sa.text("ALTER SEQUENCE audit_events_event_id_seq OWNED BY audit_events.event_id")
    )
    connection.execute(sa.text("DROP TABLE audit_events_legacy"))


def downgrade() -> None:
    connection = op.get_bind()
    if not _is_partitioned(connection):
        return

    connection.execute(sa.text("ALTER SEQUENCE audit_events_event_id_seq OWNED BY NONE"))
    connection.execute(
        sa.text(f"CREATE TABLE audit_events_legacy ({_COLUMNS}, PRIMARY KEY (event_id))")
    )
    connection.execute(sa.text(_COPY_DOWN_SQL))
    before = connection.execute(sa.text("SELECT count(*) FROM audit_events")).scalar_one()
    after = connection.execute(sa.text("SELECT count(*) FROM audit_events_legacy")).scalar_one()
    if before != after:
        raise RuntimeError(f"audit_events partition copy lost rows: {before} -> {after}")
    connection.execute(sa.text("DROP TABLE audit_events"))  # drops partitions and parent indexes
    connection.execute(sa.text("ALTER TABLE audit_events_legacy RENAME TO audit_events"))
    connection.execute(
        sa.text(
            "ALTER TABLE audit_events RENAME CONSTRAINT "
            "audit_events_legacy_pkey TO audit_events_pkey"
        )
    )
    connection.execute(
        sa.text(
            "ALTER TABLE audit_events RENAME CONSTRAINT "
            "audit_events_legacy_run_id_fkey TO audit_events_run_id_fkey"
        )
    )
    connection.execute(
        sa.text("CREATE INDEX audit_events_run_idx ON audit_events (run_id, created_at)")
    )
    connection.execute(sa.text("CREATE INDEX audit_events_trace_idx ON audit_events (trace_id)"))
    connection.execute(
        sa.text("ALTER SEQUENCE audit_events_event_id_seq OWNED BY audit_events.event_id")
    )
