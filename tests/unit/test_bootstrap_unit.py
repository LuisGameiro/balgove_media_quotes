"""Unit tests for the offline-safe Postgres bootstrap entry point."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_quotes import bootstrap
from media_quotes.config import PROJECT_ROOT


def test_resolve_sources_csv_keeps_absolute_and_anchors_relative(tmp_path: Path) -> None:
    absolute = tmp_path / "sources.csv"
    assert bootstrap.resolve_sources_csv(absolute) == absolute
    assert bootstrap.resolve_sources_csv(Path("data/sources.csv")) == (
        PROJECT_ROOT / "data/sources.csv"
    )


def test_initialize_database_uses_connection_and_counts_revisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    config = MagicMock()
    monkeypatch.setattr(bootstrap, "Config", lambda _path: config)
    upgrades: list[tuple[Any, str]] = []
    monkeypatch.setattr(bootstrap.command, "upgrade", lambda cfg, rev: upgrades.append((cfg, rev)))
    revisions = [object(), object()]
    script_directory = MagicMock()
    script_directory.walk_revisions.return_value = revisions
    from_config = MagicMock(return_value=script_directory)
    monkeypatch.setattr(bootstrap, "ScriptDirectory", MagicMock(from_config=from_config))

    ini = tmp_path / "alembic.ini"
    assert bootstrap.initialize_database(engine, ini) == 2
    assert upgrades == [(config, "head")]
    assert config.attributes == {"connection": connection}
    from_config.assert_called_once_with(config)
    engine.connect.assert_called_once_with()


def _patch_main(monkeypatch: pytest.MonkeyPatch, settings: Any, engine: Any) -> None:
    monkeypatch.setattr(bootstrap, "get_settings", lambda: settings)
    monkeypatch.setattr(bootstrap, "configure_logging", lambda _settings: None)
    monkeypatch.setattr(bootstrap, "create_db_engine", lambda _settings: engine)


def test_main_wraps_connection_errors_and_disposes_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = MagicMock()
    settings.alembic_ini = tmp_path / "alembic.ini"
    settings.database_url = "postgresql://localhost/quotes"
    engine = MagicMock()
    _patch_main(monkeypatch, settings, engine)

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(bootstrap, "initialize_database", fail)

    with pytest.raises(RuntimeError, match="Failed to connect to PostgreSQL") as exc_info:
        bootstrap.main()

    assert "docker compose up -d postgres" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    engine.dispose.assert_called_once_with()


def test_main_reraises_non_connection_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = MagicMock()
    settings.alembic_ini = tmp_path / "alembic.ini"
    engine = MagicMock()
    _patch_main(monkeypatch, settings, engine)

    def fail(*_args: Any, **_kwargs: Any) -> None:
        raise ValueError("bad configuration")

    monkeypatch.setattr(bootstrap, "initialize_database", fail)

    with pytest.raises(ValueError, match="bad configuration"):
        bootstrap.main()
    engine.dispose.assert_called_once_with()


def test_main_syncs_registry_and_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = MagicMock()
    settings.alembic_ini = tmp_path / "alembic.ini"
    engine = MagicMock()
    session = MagicMock()
    _patch_main(monkeypatch, settings, engine)
    monkeypatch.setattr(bootstrap, "initialize_database", lambda *_args, **_kwargs: 3)
    monkeypatch.setattr(bootstrap, "load_registry_rows", lambda *_args, **_kwargs: ["row"])
    monkeypatch.setattr(bootstrap, "sync_sources", lambda *_args, **_kwargs: 2)

    @contextmanager
    def fake_factory() -> Any:
        yield session

    monkeypatch.setattr(bootstrap, "make_session_factory", lambda _engine: fake_factory)

    bootstrap.main()

    assert "Bootstrap complete: 3 migration(s), 2 sources synced" in capsys.readouterr().out
    engine.dispose.assert_called_once_with()
