"""Offline tests for logging configuration and optional tracing."""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest
import structlog

from media_quotes import observability
from media_quotes.config import Settings


@pytest.mark.parametrize(
    ("log_json", "renderer_name"),
    [(True, "JSONRenderer"), (False, "ConsoleRenderer")],
)
def test_configure_logging_renderer(monkeypatch, log_json: bool, renderer_name: str) -> None:
    configure = MagicMock()
    monkeypatch.setattr(observability.structlog, "configure", configure)

    observability.configure_logging(Settings(_env_file=None, log_json=log_json))

    configure.assert_called_once()
    kwargs = configure.call_args.kwargs
    assert type(kwargs["processors"][-1]).__name__ == renderer_name
    assert isinstance(kwargs["logger_factory"], structlog.PrintLoggerFactory)
    assert kwargs["cache_logger_on_first_use"] is True


def test_configure_logging_falls_back_for_unknown_level(monkeypatch) -> None:
    configure = MagicMock()
    monkeypatch.setattr(observability.structlog, "configure", configure)

    observability.configure_logging(Settings(_env_file=None, log_level="NOT_A_LEVEL"))

    configure.assert_called_once()


def test_configure_tracing_disabled() -> None:
    assert observability.configure_tracing(Settings(_env_file=None, otel_enabled=False)) is None


def test_configure_tracing_enabled_with_fake_sdk(monkeypatch) -> None:
    tracer = object()
    trace = types.ModuleType("opentelemetry.trace")
    trace.set_tracer_provider = MagicMock()
    trace.get_tracer = MagicMock(return_value=tracer)

    otel = types.ModuleType("opentelemetry")
    otel.trace = trace

    provider = MagicMock()
    sdk_trace = types.ModuleType("opentelemetry.sdk.trace")
    sdk_trace.TracerProvider = MagicMock(return_value=provider)

    export = types.ModuleType("opentelemetry.sdk.trace.export")
    export.BatchSpanProcessor = MagicMock(return_value=MagicMock())
    export.ConsoleSpanExporter = MagicMock(return_value=MagicMock())

    monkeypatch.setitem(sys.modules, "opentelemetry", otel)
    monkeypatch.setitem(sys.modules, "opentelemetry.trace", trace)
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk", types.ModuleType("opentelemetry.sdk"))
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", sdk_trace)
    monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace.export", export)

    result = observability.configure_tracing(Settings(_env_file=None, otel_enabled=True))

    assert result is tracer
    sdk_trace.TracerProvider.assert_called_once_with()
    export.ConsoleSpanExporter.assert_called_once_with()
    export.BatchSpanProcessor.assert_called_once_with(export.ConsoleSpanExporter.return_value)
    provider.add_span_processor.assert_called_once_with(export.BatchSpanProcessor.return_value)
    trace.set_tracer_provider.assert_called_once_with(provider)
    trace.get_tracer.assert_called_once_with("media_quotes")
