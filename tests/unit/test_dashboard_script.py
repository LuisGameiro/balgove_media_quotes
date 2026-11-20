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
