"""Idempotent database writes for the source registry."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .tables import audit_events_table, decision_cache_table, pipeline_runs_table, sources_table

if TYPE_CHECKING:
    from ..registry import SourceRegistryRow


def _run_uuid(run_id: str) -> str:
    """Derive a stable UUID from the string run ids used by the CLI."""
    return str(uuid5(NAMESPACE_URL, run_id))


def start_pipeline_run(
    session: Session,
    *,
    run_id: str,
    pipeline: str,
    partition_date: date,
) -> str:
    """Insert a run row in 'running' state; idempotent on re-run."""
    values = {
        "run_id": _run_uuid(run_id),
        "pipeline": pipeline,
        "partition_date": partition_date,
        "status": "running",
        "stats": {},
    }
    statement = pg_insert(pipeline_runs_table).values(values)
    statement = statement.on_conflict_do_nothing(index_elements=["run_id"])
    session.execute(statement)
    session.commit()
    return str(values["run_id"])


def finish_pipeline_run(
    session: Session,
    *,
    run_id: str,
    status: str,
    stats: dict[str, Any],
) -> None:
    """Terminal run state (running→succeeded/failed/partial) with final stats."""
    if status not in {"succeeded", "failed", "partial"}:
        raise ValueError(f"invalid terminal run status: {status}")
    statement = pg_insert(pipeline_runs_table).values(
        run_id=_run_uuid(run_id), status=status, stats=stats
    )
    statement = statement.on_conflict_do_update(
        index_elements=["run_id"],
        set_={"status": statement.excluded.status, "stats": statement.excluded.stats},
    )
    session.execute(statement)
    session.commit()


def _event_details(event: dict[str, Any]) -> dict[str, Any]:
    reserved = {"run_id", "trace_id", "step", "entity_type", "entity_id", "status"}
    return {key: value for key, value in event.items() if key not in reserved}


def append_audit_events(session: Session, events: list[dict[str, Any]]) -> int:
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
                "details": event.get("details") or _event_details(event),
            }
        )
    session.execute(audit_events_table.insert().values(values))
    session.commit()
    return len(values)


class DecisionCacheEntry(BaseModel):
    """Immutable result of one cached LLM decision (DEC-05/B2)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    cache_key: str = Field(min_length=1)
    model_snapshot: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    schema_hash: str = Field(min_length=1)
    input_hash: str = Field(min_length=1)
    output: dict[str, Any]
    trace_id: str | None = None


def upsert_decision_cache(session: Session, entry: DecisionCacheEntry) -> bool:
    """Idempotent insert: an existing cache key is never overwritten (returns False)."""
    values = entry.model_dump(mode="json")
    statement = pg_insert(decision_cache_table).values(values)
    statement = statement.on_conflict_do_nothing(index_elements=["cache_key"])
    result = session.execute(statement)
    session.commit()
    rowcount = getattr(result, "rowcount", 0) or 0
    return rowcount > 0


def sync_sources(session: Session, rows: list[SourceRegistryRow]) -> int:
    """Upsert registry rows into Postgres. No network access."""
    if not rows:
        return 0

    values = [row.model_dump(mode="python") for row in rows]
    # Single column definition lives in tables.py; keep upserts in sync with it.
    columns = [c.name for c in sources_table.columns if c.name not in ("created_at", "updated_at")]
    statement = pg_insert(sources_table).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=["source_id"],
        set_={name: getattr(statement.excluded, name) for name in columns},
    )
    session.execute(statement)
    session.commit()
    return len(values)
