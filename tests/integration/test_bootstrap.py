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

    engine = object()
    create_db_engine = MagicMock(return_value=engine)
    monkeypatch.setattr(bootstrap, "create_db_engine", create_db_engine)

    initialize_database = MagicMock(return_value=7)
    monkeypatch.setattr(bootstrap, "initialize_database", initialize_database)

    rows = [object()]
    load_sources_csv = MagicMock(return_value=rows)
    monkeypatch.setattr(bootstrap, "load_sources_csv", load_sources_csv)

    session = MagicMock()
    factory = MagicMock(return_value=session)
    make_session_factory = MagicMock(return_value=factory)
    monkeypatch.setattr(bootstrap, "make_session_factory", make_session_factory)

    sync_sources = MagicMock(return_value=3)
    monkeypatch.setattr(bootstrap, "sync_sources", sync_sources)

    bootstrap.main()

    configure_logging.assert_called_once_with(settings)
    create_db_engine.assert_called_once_with(settings)
    initialize_database.assert_called_once_with(engine, settings.alembic_ini)
    load_sources_csv.assert_called_once_with(bootstrap.resolve_sources_csv(settings.sources_csv))
    make_session_factory.assert_called_once_with(engine)
    factory.assert_called_once_with()
    session.__enter__.assert_called_once_with()
    session.__exit__.assert_called_once()
    sync_sources.assert_called_once_with(session.__enter__.return_value, rows)
    assert "Bootstrap complete: 7 SQL statements, 3 sources synced" in capsys.readouterr().out
