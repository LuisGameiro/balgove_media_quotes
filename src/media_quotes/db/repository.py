"""Idempotent database writes for the source registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .tables import decision_cache_table, sources_table

if TYPE_CHECKING:
    from ..registry import SourceRegistryRow


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
