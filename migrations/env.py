"""Alembic environment.

The application engine is created in media_quotes.db.engine; this module only
wires Alembic's migration context. No DDL is defined here.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import Connection

from media_quotes.config import get_settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Core tables are created by op.execute() in the revisions; no ORM metadata yet.
target_metadata = None


def _database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or get_settings().database_url_value


def _run_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Emit SQL without connecting (used by `alembic upgrade head --sql`)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Use the caller's connection when provided, otherwise build one from Settings."""
    existing = config.attributes.get("connection")
    if isinstance(existing, Connection):
        _run_with_connection(existing)
        return

    engine = create_engine(_database_url(), poolclass=pool.NullPool, future=True)
    with engine.connect() as connection:
        _run_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
