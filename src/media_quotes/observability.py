"""JSON logging with secret redaction and optional OpenTelemetry tracing.

A trace_id should be propagated from orchestrator run -> fetch -> extract -> LLM ->
verifier -> digest so every quote can be traced end to end.
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING, Any, cast

import structlog
from structlog.typing import EventDict, WrappedLogger

from .config import Settings

if TYPE_CHECKING:
    from opentelemetry.sdk.trace.export import SpanExporter
    from opentelemetry.trace import Tracer

_SENSITIVE_KEYS = frozenset(
    {"api_key", "apikey", "token", "authorization", "password", "secret", "database_url"}
)
_REDACTED = "[REDACTED]"


def _redact(value: Any) -> Any:
    """Redact sensitive mapping keys, including mappings nested in lists/tuples."""
    if isinstance(value, dict):
        return {
            key: (
                _REDACTED
                if str(key).casefold().replace("-", "_") in _SENSITIVE_KEYS
                else _redact(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact(item) for item in value)
    return value


def redact_sensitive(logger: WrappedLogger, method_name: str, event_dict: EventDict) -> EventDict:
    """Redact secret-like fields before rendering; `license_reference` is audit data."""
    return cast(EventDict, _redact(event_dict))


def configure_logging(settings: Settings) -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    renderer = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            redact_sensitive,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )


def configure_tracing(settings: Settings) -> Tracer | None:
    """Return a tracer (or None when disabled). Safe no-op without the SDK."""
    if not settings.otel_enabled:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError:  # pragma: no cover - depends on optional install
        return None

    exporter: SpanExporter
    endpoint = settings.otel_exporter_otlp_endpoint
    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        except ImportError:  # pragma: no cover - core dependency installed
            return None
        exporter = OTLPSpanExporter(endpoint=endpoint)
    else:
        # Console is local-only: with no configured collector an OTLP exporter would
        # drop every span and generate connection errors.
        exporter = ConsoleSpanExporter()

    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer("media_quotes")
