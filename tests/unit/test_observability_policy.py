"""Logging redaction and tracing policy tests."""

from __future__ import annotations

from media_quotes.config import Settings
from media_quotes.observability import configure_tracing, redact_sensitive


def test_redaction_masks_credential_like_fields() -> None:
    event = {"password": "hunter2", "token": "abc", "title": "Visible"}
    assert redact_sensitive(None, "info", event) == {
        "password": "[REDACTED]",
        "token": "[REDACTED]",
        "title": "Visible",
    }


def test_tracing_honours_endpoint_setting() -> None:
    settings = Settings(
        _env_file=None,
        otel_enabled=True,
        otel_exporter_otlp_endpoint="http://localhost:4317",
    )
    assert configure_tracing(settings) is not None
