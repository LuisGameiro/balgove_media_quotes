"""The dashboard works both as an imported module and as a Streamlit script."""

from __future__ import annotations

import runpy
from pathlib import Path

DASHBOARD_SCRIPT = Path(__file__).resolve().parents[2] / "src" / "media_quotes" / "dashboard.py"


def test_dashboard_script_namespace() -> None:
    namespace = runpy.run_path(str(DASHBOARD_SCRIPT), run_name="dashboard_script_test")
    assert callable(namespace["main"])
    assert callable(namespace["guidance"])


def test_dashboard_package_import() -> None:
    from media_quotes.dashboard import guidance

    assert callable(guidance)


def test_dashboard_refuses_to_render_without_password(monkeypatch) -> None:
    import pytest

    import media_quotes.dashboard as dashboard
    from media_quotes.config import Settings

    class Stop(Exception):
        pass

    class FakeSt:
        def __init__(self) -> None:
            self.messages: list[str] = []

        def error(self, msg: str) -> None:
            self.messages.append(str(msg))

        def warning(self, msg: str) -> None:
            self.messages.append(str(msg))

        def stop(self) -> None:
            raise Stop

    fake = FakeSt()
    monkeypatch.setattr(dashboard, "st", fake)
    with pytest.raises(Stop):
        dashboard.main(settings=Settings(_env_file=None, dashboard_password=None))
    assert fake.messages and "DASHBOARD_PASSWORD" in fake.messages[0]


def test_dashboard_script_starts_main_when_run_directly(monkeypatch) -> None:
    import importlib
    from types import SimpleNamespace

    import pytest

    from media_quotes.config import Settings

    class Stop(Exception):
        pass

    class FakeSt:
        def error(self, msg: str) -> None:
            raise Stop

    real_import_module = importlib.import_module

    def fake_import_module(name: str, package: str | None = None):
        if name == "streamlit":
            return FakeSt()
        if name == "media_quotes.config":
            return SimpleNamespace(
                get_settings=lambda: Settings(_env_file=None, dashboard_password=None)
            )
        return real_import_module(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    with pytest.raises(Stop):
        runpy.run_path(str(DASHBOARD_SCRIPT), run_name="__main__")


def test_database_summary_imports_engine_when_run_as_script(monkeypatch) -> None:
    from unittest.mock import MagicMock

    from sqlalchemy.exc import SQLAlchemyError

    import media_quotes.db.engine as db_engine
    from media_quotes.config import Settings

    engine = MagicMock()
    engine.connect.side_effect = SQLAlchemyError("unreachable")
    monkeypatch.setattr(db_engine, "create_db_engine", lambda settings: engine)
    namespace = runpy.run_path(str(DASHBOARD_SCRIPT), run_name="dashboard_script_test")
    assert namespace["database_summary"](Settings(_env_file=None)) == {"error": "SQLAlchemyError"}
