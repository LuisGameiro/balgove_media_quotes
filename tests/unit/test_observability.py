"""Offline tests for logging configuration and optional tracing."""

from __future__ import annotations

import sys
import types
from typing import Any
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


def test_secret_scrub_covers_project_keys() -> None:
    event = observability.redact_sensitive(
        None, "event", {"detail": "key=sk-proj-abcdefghijklmnopqrstuvwxyz012345"}
    )
    assert "sk-proj-" not in str(event)


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


def test_configure_tracing_uses_http_exporter(monkeypatch: Any) -> None:
    import opentelemetry.sdk.trace.export as export
    import opentelemetry.trace as trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    monkeypatch.setattr(export, "BatchSpanProcessor", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(trace, "set_tracer_provider", MagicMock())
    settings = Settings(
        _env_file=None,
        otel_enabled=True,
        otel_exporter_otlp_endpoint="http://127.0.0.1:4318/v1/traces",
    )

    assert observability.configure_tracing(settings) is not None
    exporter = export.BatchSpanProcessor.call_args.args[0]
    assert isinstance(exporter, OTLPSpanExporter)


def test_cli_entry_configures_observability_once(monkeypatch: Any) -> None:
    from media_quotes import pipeline
    from media_quotes.cli import app

    settings = Settings(_env_file=None)
    calls: list[Settings] = []
    monkeypatch.setattr(app, "get_settings", lambda: settings)
    monkeypatch.setattr(app, "configure_observability", calls.append)
    monkeypatch.setattr(app, "main", lambda argv: 0)

    assert app.cli_entry([]) == 0
    assert calls == [settings]
    assert not hasattr(pipeline, "configure_observability")  # not per pipeline run
