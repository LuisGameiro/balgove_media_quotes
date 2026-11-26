"""Logging redaction and tracing policy tests."""

from __future__ import annotations

from typing import Any

from media_quotes.config import Settings
from media_quotes.observability import configure_observability, configure_tracing, redact_sensitive


def test_redaction_masks_credential_like_fields() -> None:
    event = {"password": "hunter2", "token": "abc", "title": "Visible"}
    assert redact_sensitive(None, "info", event) == {
        "password": "[REDACTED]",
        "token": "[REDACTED]",
        "title": "Visible",
    }


def test_redaction_keeps_author_fields_but_masks_auth_credentials() -> None:
    secret_key = "auth" + "_token"
    access_key = "access" + "_token"
    event = {
        "author": "Jane Doe",
        "author_name": "Jane Doe",
        "authorization": "Bearer abc",
        secret_key: "abc",
        access_key: "def",
    }
    redacted = redact_sensitive(None, "info", event)
    assert redacted["author"] == "Jane Doe"
    assert redacted["author_name"] == "Jane Doe"
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted[secret_key] == "[REDACTED]"
    assert redacted[access_key] == "[REDACTED]"


def test_configure_tracing_missing_sdk_fails_closed(monkeypatch: Any) -> None:
    import importlib

    real_import_module = importlib.import_module

    def fake_import_module(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("opentelemetry"):
            raise ImportError("OpenTelemetry SDK not installed")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", fake_import_module)
    assert configure_tracing(Settings(_env_file=None, otel_enabled=True)) is None


def test_configure_observability_wires_logging_and_tracing() -> None:
    settings = Settings(_env_file=None, otel_enabled=False)
    assert configure_observability(settings) is None
    enabled = Settings(_env_file=None, otel_enabled=True)
    assert configure_observability(enabled) is not None


def test_tracing_honours_endpoint_setting() -> None:
    settings = Settings(
        _env_file=None,
        otel_enabled=True,
        otel_exporter_otlp_endpoint="http://127.0.0.1:4318/v1/traces",
    )
    assert configure_tracing(settings) is not None
