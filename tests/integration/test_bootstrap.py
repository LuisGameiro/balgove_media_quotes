"""Offline tests for the bootstrap CLI entry point."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from media_quotes import bootstrap
from media_quotes.config import Settings


def test_resolve_sources_csv_absolute_path(tmp_path: Path) -> None:
    configured = tmp_path / "sources.csv"
    assert bootstrap.resolve_sources_csv(configured) == configured


def test_resolve_sources_csv_relative_path() -> None:
    configured = Path("data/sources.csv")
    expected = Path(bootstrap.__file__).resolve().parents[2] / configured
    assert bootstrap.resolve_sources_csv(configured) == expected


def test_main_runs_full_bootstrap_pipeline(monkeypatch, capsys) -> None:
    settings = Settings(_env_file=None, sources_csv=Path("data/sources.csv"))
    monkeypatch.setattr(bootstrap, "get_settings", lambda: settings)

    configure_logging = MagicMock()
    monkeypatch.setattr(bootstrap, "configure_logging", configure_logging)

    engine = MagicMock()
    create_db_engine = MagicMock(return_value=engine)
    monkeypatch.setattr(bootstrap, "create_db_engine", create_db_engine)

    initialize_database = MagicMock(return_value=1)
    monkeypatch.setattr(bootstrap, "initialize_database", initialize_database)

    rows = [object()]
    load_registry_rows = MagicMock(return_value=rows)
    monkeypatch.setattr(bootstrap, "load_registry_rows", load_registry_rows)

    session = MagicMock()
    factory = MagicMock(return_value=session)
    make_session_factory = MagicMock(return_value=factory)
    monkeypatch.setattr(bootstrap, "make_session_factory", make_session_factory)

    sync_sources = MagicMock(return_value=3)
    monkeypatch.setattr(bootstrap, "sync_sources", sync_sources)

    bootstrap.main()

    configure_logging.assert_called_once_with(settings)
    create_db_engine.assert_called_once_with(settings)
    initialize_database.assert_called_once_with(engine)
    load_registry_rows.assert_called_once_with(
        settings.sources_dir, fallback_csv=settings.sources_csv
    )
    engine.dispose.assert_called_once_with()
    make_session_factory.assert_called_once_with(engine)
    factory.assert_called_once_with()
    session.__enter__.assert_called_once_with()
    session.__exit__.assert_called_once()
    sync_sources.assert_called_once_with(session.__enter__.return_value, rows)
    assert "Bootstrap complete: 1 schema script, 3 sources synced" in capsys.readouterr().out


def test_initialize_database_executes_schema_once(tmp_path: Path) -> None:
    engine = MagicMock()
    connection = engine.begin.return_value.__enter__.return_value
    schema = tmp_path / "schema.sql"
    schema.write_text("SELECT format('%L', 'x');", encoding="utf-8")

    assert bootstrap.initialize_database(engine, schema) == 1

    connection.exec_driver_sql.assert_called_once_with("SELECT format('%%L', 'x');")


def _harness(monkeypatch, tmp_path, present_columns):
    from sqlalchemy.dialects import postgresql

    schema = tmp_path / "schema.sql"
    schema.write_text("SELECT 1;", encoding="utf-8")

    connection = MagicMock()
    connection.dialect = postgresql.dialect()
    engine = MagicMock()
    engine.begin.return_value.__enter__.return_value = connection

    class FakeInspector:
        def get_table_names(self):
            return ["articles"]

        def get_columns(self, name):
            assert name == "articles"
            return [{"name": column} for column in present_columns]

    monkeypatch.setattr(bootstrap, "inspect", lambda _connection: FakeInspector())
    return engine, connection, schema


def test_initialize_database_adds_missing_model_column(monkeypatch, tmp_path) -> None:
    from media_quotes.db.tables import articles_table

    present = [c.name for c in articles_table.columns if c.name != "provenance"]
    engine, connection, schema = _harness(monkeypatch, tmp_path, present)

    assert bootstrap.initialize_database(engine, schema) == 1

    executed = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    alters = [sql for sql in executed if sql.startswith("ALTER TABLE")]
    assert alters == [
        "ALTER TABLE articles ADD COLUMN IF NOT EXISTS "
        "provenance VARCHAR NOT NULL DEFAULT 'fixture'"
    ]


def test_initialize_database_leaves_matching_columns(monkeypatch, tmp_path) -> None:
    from media_quotes.db.tables import articles_table

    present = [c.name for c in articles_table.columns]
    engine, connection, schema = _harness(monkeypatch, tmp_path, present)

    assert bootstrap.initialize_database(engine, schema) == 1

    executed = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert [sql for sql in executed if sql.startswith("ALTER TABLE")] == []
