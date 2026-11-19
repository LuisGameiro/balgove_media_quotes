"""Idempotent database writes for the source registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .tables import sources_table

if TYPE_CHECKING:
    from ..registry import SourceRegistryRow


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
