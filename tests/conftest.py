"""Shared pytest fixtures and warning policy (docs/02 §7).

Unit tests stay offline: no network and no database. Integration/network/slow markers
are declared in pyproject.toml and must be opted into explicitly.
"""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest
from _pytest.config import Config

ROOT = Path(__file__).resolve().parents[1]


def pytest_configure(config: Config) -> None:
    # Our own DeprecationWarnings are errors; third-party ones are narrowed by message.
    warnings.filterwarnings("error", category=DeprecationWarning, module=r"^media_quotes\.")
    warnings.filterwarnings(
        "ignore",
        message=r"datetime\.utcnow\(\) is deprecated",
        category=DeprecationWarning,
    )
    warnings.filterwarnings(
        "ignore",
        message=r"datetime\.datetime\.utcfromtimestamp\(\) is deprecated.*",
        category=DeprecationWarning,
    )

    # trafilatura imports urllib3 SOCKS support; PySocks is optional and unused offline.
    warnings.filterwarnings(
        "ignore",
        category=Warning,
        module=r"urllib3\.contrib\.socks",
    )


@pytest.fixture
def project_root() -> Path:
    return ROOT


@pytest.fixture
def data_dir(project_root: Path) -> Path:
    return project_root / "data"


@pytest.fixture
def sources_csv(data_dir: Path) -> Path:
    return data_dir / "sources.csv"


@pytest.fixture
def sections_csv(data_dir: Path) -> Path:
    return data_dir / "sections.csv"
