from __future__ import annotations

from media_quotes import cli
from media_quotes.config import PROJECT_ROOT, Settings


def test_doctor_reports_valid_data(capsys) -> None:
    assert cli.main(["doctor"]) == 0
    assert "All data files valid" in capsys.readouterr().out


def test_doctor_defaults_dagster_home_beside_output_dir(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("DAGSTER_HOME", raising=False)
    settings = Settings(_env_file=None, output_dir=tmp_path / "runtime")

    assert cli._do_doctor(settings) == 0

    expected = tmp_path / "dagster"
    assert f"dagster_home={expected}" in capsys.readouterr().out


def test_validate_alias(capsys) -> None:
    assert cli.main(["validate"]) == 0
    assert "All data files valid" in capsys.readouterr().out


def test_bootstrap_command_is_offline(monkeypatch) -> None:
    monkeypatch.setattr(cli, "_bootstrap", lambda: 7)
    assert cli.main(["bootstrap"]) == 7


def test_data_dir_points_at_project_data() -> None:
    assert PROJECT_ROOT / "data" == cli.DATA_DIR
