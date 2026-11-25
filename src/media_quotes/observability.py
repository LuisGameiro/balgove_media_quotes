"""Structured logging and optional OpenTelemetry distributed tracing."""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from typing import Any

import structlog

from .config import Settings

_SENSITIVE_PATTERNS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "access_token",
    "database_url",
    "private_key",
)


def _is_sensitive_key(key: Any) -> bool:
    norm = str(key).casefold().replace("-", "_")
    return any(p in norm for p in _SENSITIVE_PATTERNS)


_SECRET_STRING_PATTERNS = re.compile(
    r"("
    r"[a-z][a-z0-9+.-]*://[^:@/\s]+:[^@/\s]+@"
    r"|Bearer\s+[A-Za-z0-9._~+/-]+"
    r"|Basic\s+[A-Za-z0-9+/=]+"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r")",
    re.IGNORECASE,
)


def _scrub_string(s: str) -> str:
    return _SECRET_STRING_PATTERNS.sub("[REDACTED_SECRET]", s)


def _redact(value: Any) -> Any:
    """Redact sensitive mapping keys, string secrets, and collections."""
    if isinstance(value, str):
        return _scrub_string(value)
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for k, v in value.items():
            if _is_sensitive_key(k):
                redacted[str(k)] = "[REDACTED]"
            else:
                redacted[str(k)] = _redact(v)
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [_redact(item) for item in value]
    return value


def redact_event_dict(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> dict[str, Any]:
    """Structlog processor that redacts secrets before output formatting."""
    return _redact(dict(event_dict))  # type: ignore[no-any-return]


# Backwards-compatible name used by older callers and tests.
redact_sensitive = redact_event_dict


def configure_logging(settings: Settings) -> None:
    """Configure stdlib and structlog logging handlers and formatters."""
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        redact_event_dict,
    ]

    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stderr,
        level=log_level,
    )

    if settings.log_json:
        processors: list[structlog.types.Processor] = [
            *shared_processors,
            structlog.processors.JSONRenderer(),
        ]
    else:
        processors = [*shared_processors, structlog.dev.ConsoleRenderer()]

    structlog.configure(
        processors=processors,
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )


def configure_tracing(settings: Settings) -> Any | None:
    """Configure OpenTelemetry tracing if enabled; return the tracer when active."""
    if not settings.otel_enabled:
        return None

    from importlib import import_module

    try:
        trace = import_module("opentelemetry.trace")
        provider = import_module("opentelemetry.sdk.trace").TracerProvider()
        export = import_module("opentelemetry.sdk.trace.export")
        if settings.otel_exporter_otlp_endpoint:
            otlp = import_module("opentelemetry.exporter.otlp.proto.http.trace_exporter")
            exporter = otlp.OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
        else:
            exporter = export.ConsoleSpanExporter()
        provider.add_span_processor(export.BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        return trace.get_tracer("media_quotes")
    except ImportError:
        # Fail closed: OTEL_ENABLED without the SDK must not crash process startup.
        logging.getLogger(__name__).warning(
            "OTEL_ENABLED=true but OpenTelemetry packages are unavailable; tracing disabled"
        )
        return None


def configure_observability(settings: Settings) -> Any | None:
    """Combined logging and tracing entry point."""
    configure_logging(settings)
    return configure_tracing(settings)
