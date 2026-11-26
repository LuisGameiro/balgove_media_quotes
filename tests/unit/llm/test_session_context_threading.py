"""Injected session contexts reach every DbDecisionCache constructor (offline)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from conftest import llm_settings
from media_quotes.config import Settings


@pytest.fixture(autouse=True)
def _stub_openrouter_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep provider construction offline when the optional `openai` extra is absent.

    The assertions here are about session-context threading, not SDK behavior, so every
    module-level client seam is replaced with a lightweight fake.
    """
    import importlib

    fake_client = object()
    for module_name in (
        "media_quotes.ai.llm_openrouter",
        "media_quotes.ai.translation",
        "media_quotes.ai.judge",
        "media_quotes.ai.quote_audit",
    ):
        module = importlib.import_module(module_name)
        if hasattr(module, "make_openrouter_client"):
            monkeypatch.setattr(
                module, "make_openrouter_client", lambda _settings, _fake=fake_client: _fake
            )


def _settings(**overrides: object) -> Settings:
    values: dict[str, Any] = {
        "openrouter_api_key": "test-key",  # type: ignore[arg-type]
        "ai_judge_mode": "advisory",
        "ai_judge_model_snapshot": "openai/gpt-4o-mini-2024-07-18",
        "persist_to_postgres": True,
        "translation_mode": "translate",
        "report_date": date(2026, 11, 19),
    }
    values.update(overrides)
    return llm_settings(**values)


@pytest.mark.parametrize(
    ("module", "factory"),
    [
        ("media_quotes.ai.llm", "make_provider"),
        ("media_quotes.ai.judge", "make_judge"),
        ("media_quotes.ai.translation", "make_translation_provider"),
    ],
)
def test_factories_pass_session_context_to_cache(
    monkeypatch: pytest.MonkeyPatch, module: str, factory: str
) -> None:
    import importlib

    target = importlib.import_module(module)
    cache_module = importlib.import_module("media_quotes.ai.llm_cache")
    captured: list[Any] = []

    class RecordingCache:
        def __init__(self, settings: Settings, *, session_context: Any = None) -> None:
            captured.append(session_context)

    monkeypatch.setattr(cache_module, "DbDecisionCache", RecordingCache)
    context = MagicMock()
    getattr(target, factory)(_settings(), session_context=context)
    assert captured == [context]


def test_quote_audit_client_passes_session_context(monkeypatch: pytest.MonkeyPatch) -> None:
    from media_quotes.ai import llm_cache, quote_audit

    captured: list[Any] = []

    class RecordingCache:
        def __init__(self, settings: Settings, *, session_context: Any = None) -> None:
            captured.append(session_context)

    monkeypatch.setattr(llm_cache, "DbDecisionCache", RecordingCache)
    context = MagicMock()
    quote_audit._client(_settings(), session_context=context)
    assert captured == [context]


def test_call_factory_binds_context_and_propagates_errors() -> None:
    from media_quotes.pipeline import _call_factory

    settings = _settings()
    context = MagicMock()

    def one_arg(factory_settings: Settings, **kwargs: Any) -> str:
        assert factory_settings is settings
        assert kwargs["session_context"] is context
        return "one"

    assert _call_factory(one_arg, settings, session_context=context) == "one"

    seen: list[Any] = []

    def with_context(factory_settings: Settings, *, session_context: Any = None) -> str:
        seen.append(session_context)
        return "two"

    assert _call_factory(with_context, settings, session_context=context) == "two"
    assert seen == [context]

    def raising(factory_settings: Settings, **kwargs: Any) -> None:
        raise TypeError("real factory error")

    with pytest.raises(TypeError, match="real factory error"):
        _call_factory(raising, settings, session_context=context)


def test_call_factory_rejects_factory_without_context_slot() -> None:
    from media_quotes.pipeline import _call_factory

    def incompatible(factory_settings: Settings) -> str:
        return "unexpected"

    with pytest.raises(TypeError, match="unexpected keyword argument 'session_context'"):
        _call_factory(incompatible, _settings(), session_context=MagicMock())


def test_collect_passes_session_context_to_translation_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_quotes.cli import run as run_cli

    context = MagicMock()
    captured: list[Any] = []

    class NoopProvider:
        pass

    def factory(settings: Settings, *, session_context: Any = None) -> NoopProvider:
        captured.append(session_context)
        return NoopProvider()

    monkeypatch.setattr("media_quotes.ingestion.collect.make_translation_provider", factory)
    monkeypatch.setattr("media_quotes.ingestion.collect._load_registry", lambda settings: [])
    run_cli._collect(
        _settings(export_dir=tmp_path / "export"),
        run_id="r",
        report_date=date(2026, 11, 19),
        source_mode="fixtures",
        session_context=context,
    )
    assert captured == [context]
