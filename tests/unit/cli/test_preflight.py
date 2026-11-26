"""Preflight reports readiness without spending money or requiring live services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from conftest import llm_settings, settings_for
from media_quotes.cli.common import EXIT_FAILURE, EXIT_OK
from media_quotes.cli.preflight import _do_preflight


class _Connection:
    def __enter__(self) -> _Connection:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, statement: object) -> None:
        return None


class _Engine:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    def connect(self) -> _Connection:
        if self.fail:
            raise RuntimeError("database is down")
        return _Connection()

    def dispose(self) -> None:
        return None


def test_default_profile_needs_no_services(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _do_preflight(settings_for(tmp_path), probe=False) == EXIT_OK
    out = capsys.readouterr().out
    assert "data files" in out and "ok" in out
    assert "postgres" in out and "disabled" in out
    assert "neo4j" in out and "disabled" in out
    assert "llm / openrouter" in out and "disabled" in out


def test_postgres_probe_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import media_quotes.db.engine as engine_module

    settings = settings_for(tmp_path, persist_to_postgres=True)
    monkeypatch.setattr(engine_module, "create_db_engine", lambda _settings: _Engine())

    assert _do_preflight(settings, probe=True) == EXIT_OK
    assert "SELECT 1 succeeded" in capsys.readouterr().out

    monkeypatch.setattr(engine_module, "create_db_engine", lambda _settings: _Engine(fail=True))
    assert _do_preflight(settings, probe=True) == EXIT_FAILURE
    out = capsys.readouterr().out
    assert "unreachable" in out and "database is down" in out


def test_llm_missing_cache_is_a_blocker(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings: Any = llm_settings()
    assert _do_preflight(settings, probe=False) == EXIT_FAILURE
    out = capsys.readouterr().out
    assert "misconfigured" in out
    assert "PERSIST_TO_POSTGRES" in out
