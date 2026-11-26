from __future__ import annotations

import pytest

from media_quotes import cli
from media_quotes.config import PROJECT_ROOT


def _invalid_llm_env(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")


def test_doctor_reports_valid_data(capsys) -> None:
    assert cli.main(["doctor"]) == 0
    assert "All data files valid" in capsys.readouterr().out


def test_validate_alias(capsys) -> None:
    assert cli.main(["validate"]) == 0
    assert "All data files valid" in capsys.readouterr().out


def test_bootstrap_command_is_offline(monkeypatch) -> None:
    monkeypatch.setattr("media_quotes.cli.app._bootstrap", lambda: 7)
    assert cli.main(["bootstrap"]) == 7


def test_data_dir_points_at_project_data() -> None:
    assert PROJECT_ROOT / "data" == cli.DATA_DIR


def test_cli_uses_central_project_root() -> None:
    assert cli.DATA_DIR == PROJECT_ROOT / "data"


def test_help_does_not_load_settings_with_invalid_env(monkeypatch, capsys) -> None:
    _invalid_llm_env(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        cli.cli_entry(["--help"])
    assert exc.value.code == 0
    assert "usage:" in capsys.readouterr().out


def test_doctor_with_invalid_settings_returns_config_error(monkeypatch, capsys) -> None:
    _invalid_llm_env(monkeypatch)
    assert cli.cli_entry(["doctor"]) == cli.EXIT_CONFIG
    err = capsys.readouterr().err
    assert "error:" in err
    assert "OPENROUTER_API_KEY" in err
    assert "Traceback" not in err
