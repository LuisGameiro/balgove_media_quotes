"""Pipeline run and audit-event persistence."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ...config import DEFAULT_TOPIC_ID
from ..tables import audit_events_table, pipeline_runs_table, storage_partitions_table
from .common import _inserted_count, _jsonable, _run_uuid


def _event_details(event: dict[str, Any]) -> dict[str, Any]:
    reserved = {"run_id", "trace_id", "step", "entity_type", "entity_id", "status"}
    return {key: value for key, value in event.items() if key not in reserved}


def start_pipeline_run(
    session: Session,
    *,
    run_id: str,
    pipeline: str,
    partition_date: date,
    topic_id: str = DEFAULT_TOPIC_ID,
    stats: dict[str, Any] | None = None,
    commit: bool = True,
) -> str:
    """Insert a run row in 'running' state; idempotent on re-run."""
    values = {
        "run_id": _run_uuid(run_id),
        "pipeline": pipeline,
        "partition_date": partition_date,
        "status": "running",
        "stats": _jsonable(stats or {}),
        "topic_id": topic_id,
    }
    statement = pg_insert(pipeline_runs_table).values(values)
    statement = statement.on_conflict_do_nothing(index_elements=["run_id"])
    session.execute(statement)
    if commit:
        session.commit()
    return str(values["run_id"])


def finish_pipeline_run(
    session: Session,
    *,
    run_id: str,
    status: str,
    stats: dict[str, Any],
    commit: bool = True,
) -> None:
    """Terminal run state (running→succeeded/failed/partial) with final stats."""
    if status not in {"succeeded", "failed", "partial"}:
        raise ValueError(f"invalid terminal run status: {status}")
    statement = (
        update(pipeline_runs_table)
        .where(pipeline_runs_table.c.run_id == _run_uuid(run_id))
        .values(status=status, stats=_jsonable(stats), finished_at=datetime.now(UTC))
    )
    session.execute(statement)
    if commit:
        session.commit()


def append_audit_events(
    session: Session, events: list[dict[str, Any]], *, commit: bool = True
) -> int:
    """Append audit/coverage events; never updates existing rows."""
    if not events:
        return 0
    values = []
    for event in events:
        values.append(
            {
                "run_id": _run_uuid(str(event["run_id"])) if event.get("run_id") else None,
                "trace_id": event.get("trace_id"),
                "step": str(event.get("step") or "unknown"),
                "entity_type": event.get("entity_type"),
                "entity_id": event.get("entity_id"),
                "status": str(event.get("status") or "unknown"),
                "details": _jsonable(event.get("details") or _event_details(event)),
            }
        )
    session.execute(audit_events_table.insert().values(values))
    if commit:
        session.commit()
    return len(values)


def read_audit_events(
    session: Session, run_id: str, *, trace_id: str | None = None
) -> list[dict[str, Any]]:
    """Read persisted audit events for one run, optionally filtered by trace id."""
    statement = select(
        audit_events_table.c.step,
        audit_events_table.c.status,
        audit_events_table.c.entity_type,
        audit_events_table.c.entity_id,
        audit_events_table.c.trace_id,
        audit_events_table.c.details,
    ).where(audit_events_table.c.run_id == _run_uuid(run_id))
    if trace_id is not None:
        statement = statement.where(audit_events_table.c.trace_id == trace_id)
    statement = statement.order_by(audit_events_table.c.event_id)
    return [dict(row) for row in session.execute(statement).mappings()]


def sync_storage_partitions(session: Session, *, commit: bool = True) -> int:
    """Register observed audit_event months in storage_partitions as hot.

    No DDL and no data movement: this only records which partitions exist so the
    retention planner and operator have one source of truth.
    """
    from sqlalchemy import func

    months = (
        session.execute(
            select(
                func.date_trunc(
                    "month", audit_events_table.c.created_at.op("AT TIME ZONE")("UTC")
                ).label("month")
            ).distinct()
        )
        .scalars()
        .all()
    )
    if not months:
        return 0
    rows = [
        {"table_name": "audit_events", "month": month.date(), "state": "hot"}
        for month in months
        if month is not None
    ]
    statement = pg_insert(storage_partitions_table).values(rows)
    statement = statement.on_conflict_do_nothing(index_elements=["table_name", "month"])
    result = session.execute(statement)
    if commit:
        session.commit()
    return _inserted_count(result, len(rows))
