"""Shared pytest fixtures and warning policy (docs/02 §7).

Unit tests stay offline: no network and no database. Integration/network/slow markers
are declared in pyproject.toml and must be opted into explicitly.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest
from _pytest.config import Config
from pydantic import SecretStr

from media_quotes.config import Settings

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


@pytest.fixture(autouse=True)
def isolate_unit_test_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate unit tests from host .env and ensure fail-closed offline defaults."""
    monkeypatch.setenv("PERSIST_TO_POSTGRES", "false")
    monkeypatch.setenv("LLM_PROVIDER", "local")
    monkeypatch.setenv("SYNC_TO_NEO4J", "false")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    yield


@pytest.fixture(autouse=True)
def isolate_plugin_registries() -> Iterator[None]:
    """Registries are process-global; snapshot and restore them around every test."""
    from media_quotes.adapters import registry

    adapters = dict(registry._ADAPTERS)
    sources = dict(registry._SOURCE_ADAPTERS)
    yield
    registry._ADAPTERS.clear()
    registry._ADAPTERS.update(adapters)
    registry._SOURCE_ADAPTERS.clear()
    registry._SOURCE_ADAPTERS.update(sources)


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    """Offline Settings with repo data paths and temp output dirs."""
    values: dict[str, object] = {
        "sources_csv": ROOT / "data/sources.csv",
        "sources_dir": ROOT / "data/sources",
        "sections_csv": ROOT / "data/sections.csv",
        "source_topics_csv": ROOT / "data/source_topic_routes.csv",
        "topic_rules_path": ROOT / "data/topic_rules.json",
        "topics_dir": ROOT / "data/topics",
        "schema_path": ROOT / "data/quotes.schema.json",
        "fixtures_dir": ROOT / "data/fixtures",
        "gold_set_dir": ROOT / "data/gold_set",
        "audit_dir": tmp_path / "audit",
        "export_dir": tmp_path / "export",
        "digest_dir": tmp_path / "digests",
        "blob_dir": tmp_path / "blobs",
        "logs_dir": tmp_path / "logs",
        "persist_to_postgres": False,
        "llm_provider": "local",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def llm_settings(**overrides: object) -> Settings:
    """Offline OpenRouter Settings for the LLM/judge cluster."""
    values: dict[str, object] = {
        "llm_provider": "openrouter",
        "llm_model_snapshot": "openai/gpt-4o-mini-2024-07-18",
        "openrouter_api_key": SecretStr("test-key"),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


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
