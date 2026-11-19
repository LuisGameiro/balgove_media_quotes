"""Offline tests for the database bootstrapping helpers (engine and schema loader)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from media_quotes.config import Settings
from media_quotes.db import engine as db_engine
from media_quotes.db.repository import sync_sources
from media_quotes.db.tables import sources_table
from media_quotes.registry import SourceRegistryRow, load_sources_csv

SOURCES_CSV = Path(__file__).resolve().parents[2] / "data" / "sources.csv"


def test_create_db_engine_uses_settings(monkeypatch) -> None:
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://u:p@host/db")
    create_engine = MagicMock()
    monkeypatch.setattr(db_engine, "create_engine", create_engine)

    result = db_engine.create_db_engine(settings)

    create_engine.assert_called_once_with(
        settings.database_url_value, pool_pre_ping=True, future=True
    )
    assert result is create_engine.return_value


def test_make_session_factory_uses_engine(monkeypatch) -> None:
    engine = object()
    sessionmaker = MagicMock()
    monkeypatch.setattr(db_engine, "sessionmaker", sessionmaker)

    result = db_engine.make_session_factory(engine)

    sessionmaker.assert_called_once_with(bind=engine, expire_on_commit=False, future=True)
    assert result is sessionmaker.return_value


def test_engine_never_executes_raw_ddl() -> None:
    """DEC-15: bootstrap delegates DDL to Alembic, not the engine module."""
    assert not hasattr(db_engine, "initialize_database")
    assert not hasattr(db_engine, "_SCHEMA")


def test_engine_has_no_schema_loader_side_effects() -> None:
    """The engine module creates engines/sessions only; schema setup lives in bootstrap/Alembic."""
    assert set(dir(db_engine)).isdisjoint({"_SCHEMA", "initialize_database"})
    assert callable(db_engine.create_db_engine)
    assert callable(db_engine.make_session_factory)


def test_sources_table_definition() -> None:
    assert sources_table.name == "sources"
    names = [column.name for column in sources_table.columns]
    assert "source_id" in names
    assert "created_at" not in names
    assert "updated_at" not in names


def test_sync_sources_with_empty_rows() -> None:
    session = MagicMock()
    assert sync_sources(session, []) == 0
    session.execute.assert_not_called()
    session.commit.assert_not_called()


def test_sync_sources_upserts_rows_without_database() -> None:
    rows = load_sources_csv(SOURCES_CSV)[:2]
    assert all(isinstance(row, SourceRegistryRow) for row in rows)
    session = MagicMock()

    assert sync_sources(session, rows) == len(rows)
    session.execute.assert_called_once()
    session.commit.assert_called_once()
