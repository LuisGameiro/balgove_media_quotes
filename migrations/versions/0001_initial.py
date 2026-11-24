"""Initial append-oriented schema.

`src/media_quotes/db/schema.sql` is the single source of truth for the DDL:
this revision emits each statement with `op.execute`, including
`quotes.canonical_quote_id` (format Q-YYYYMMDD-NNNN).
"""

from __future__ import annotations

import importlib.resources
from collections.abc import Iterator
from pathlib import Path

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

_SCHEMA_SQL = Path(__file__).resolve().parents[2] / "src/media_quotes/db/schema.sql"

_DROP_STATEMENTS = (
    "DROP TABLE IF EXISTS audit_events;",
    "DROP TABLE IF EXISTS digests;",
    "DROP TABLE IF EXISTS obsolescence_flags;",
    "DROP TABLE IF EXISTS quote_classifications;",
    "DROP TABLE IF EXISTS quotes;",
    "DROP TABLE IF EXISTS articles;",
    "DROP TABLE IF EXISTS pipeline_runs;",
    "DROP TABLE IF EXISTS sources;",
    "DROP TABLE IF EXISTS gold_set_labels;",
    "DROP TABLE IF EXISTS eval_runs;",
    "DROP TABLE IF EXISTS decision_cache;",
    "DROP TABLE IF EXISTS storage_partitions;",
)


def _statements(sql: str) -> Iterator[str]:
    """Split schema.sql on semicolons at line ends (the DDL has no embedded ';')."""
    buffer: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buffer.append(line)
        if line.rstrip().endswith(";"):
            statement = "\n".join(buffer).strip()
            if statement and statement != ";":
                yield statement
            buffer = []
    tail = "\n".join(buffer).strip()
    if tail:
        yield tail


def _load_schema_sql() -> str:
    if _SCHEMA_SQL.is_file():
        return _SCHEMA_SQL.read_text(encoding="utf-8")
    try:
        sql_file = importlib.resources.files("media_quotes.db").joinpath("schema.sql")
        return sql_file.read_text(encoding="utf-8")
    except Exception as exc:
        msg = f"schema.sql not found at {_SCHEMA_SQL} or in package resources"
        raise FileNotFoundError(msg) from exc


def upgrade() -> None:
    for statement in _statements(_load_schema_sql()):
        op.execute(statement)


def downgrade() -> None:
    # The pgcrypto extension is shared infrastructure; do not drop it here.
    for statement in _DROP_STATEMENTS:
        op.execute(statement)
