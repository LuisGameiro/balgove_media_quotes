"""CLI: python -m media_quotes.bootstrap

1. Applies the consolidated schema SQL once (idempotently).
2. Syncs the authoritative source registry from data/sources.csv.

No network access, no source contact. The schema file is the only DDL source.
"""

import json
from pathlib import Path
from typing import Any

from sqlalchemy import ARRAY, JSON, Boolean, Column, Float, Integer, inspect
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import NoInspectionAvailable

from .config import PROJECT_ROOT, get_settings
from .db.engine import create_db_engine, make_session_factory
from .db.repository import sync_sources
from .db.tables import metadata
from .observability import configure_logging
from .source_configs import load_registry_rows

_SCHEMA_SQL = PROJECT_ROOT / "migrations" / "schema.sql"


def resolve_sources_csv(configured: Path) -> Path:
    if configured.is_absolute():
        return configured
    return PROJECT_ROOT / configured


def _literal_sql(value: object) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, (dict, list)):
        return "'" + json.dumps(value).replace("'", "''") + "'::jsonb"
    return "''"


def _empty_default_sql(column: Column[Any]) -> str:
    if isinstance(column.type, JSON):
        if column.default is not None and getattr(column.default, "arg", None) is list:
            return "'[]'::jsonb"
        return "'{}'::jsonb"
    if isinstance(column.type, ARRAY):
        return "'{}'"
    if isinstance(column.type, Boolean):
        return "FALSE"
    if isinstance(column.type, Integer):
        return "0"
    if isinstance(column.type, Float):
        return "0.0"
    return "''"


def _default_sql(column: Column[Any]) -> str:
    server_default = column.server_default
    if server_default is not None:
        return str(getattr(server_default, "arg", server_default))
    default = column.default
    arg = getattr(default, "arg", None)
    if default is not None and arg is not None and not callable(arg):
        return _literal_sql(arg)
    return _empty_default_sql(column)


def _add_missing_columns(connection: Connection) -> None:
    """Reconcile model columns onto existing tables after IF NOT EXISTS skipped them."""
    try:
        inspector = inspect(connection)
    except NoInspectionAvailable:
        return
    existing = set(inspector.get_table_names())
    for table in metadata.tables.values():
        if table.name not in existing:
            continue
        present = {info["name"] for info in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            definition = column.type.compile(dialect=connection.dialect)
            not_null = " NOT NULL" if not column.nullable else ""
            connection.exec_driver_sql(
                f"ALTER TABLE {table.name} ADD COLUMN IF NOT EXISTS "
                f"{column.name} {definition}{not_null} DEFAULT {_default_sql(column)}"
            )


def initialize_database(engine: Engine, schema_sql: Path | None = None) -> int:
    """Apply the consolidated schema through a caller-owned engine."""
    path = schema_sql or _SCHEMA_SQL
    # SQLAlchemy uses pyformat placeholders; escape PostgreSQL format() specifiers.
    sql = path.read_text(encoding="utf-8").replace("%", "%%")
    with engine.begin() as connection:
        connection.exec_driver_sql(sql)
        _add_missing_columns(connection)
    return 1


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    engine = create_db_engine(settings)
    try:
        scripts = initialize_database(engine)
        rows = load_registry_rows(settings.sources_dir, fallback_csv=settings.sources_csv)

        with make_session_factory(engine)() as session:
            synced = sync_sources(session, rows)
    except Exception as exc:
        msg = str(exc).lower()
        if (
            "timeout" in msg
            or "connection refused" in msg
            or "could not connect" in msg
            or "operationalerror" in type(exc).__name__.lower()
        ):
            raise RuntimeError(
                f"Failed to connect to PostgreSQL ({settings.database_url}): {exc}. "
                "Ensure the PostgreSQL container is running "
                "(e.g. 'docker compose up -d postgres') and accessible."
            ) from exc
        raise
    finally:
        engine.dispose()

    print(f"Bootstrap complete: {scripts} schema script, {synced} sources synced")


if __name__ == "__main__":
    main()
