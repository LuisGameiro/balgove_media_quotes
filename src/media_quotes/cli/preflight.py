"""Readiness report for optional services and features (no paid calls)."""

from __future__ import annotations

import importlib.util
from collections.abc import Callable

from sqlalchemy import text

from ..config import Settings
from ..validation import validate_data_dir
from .common import DATA_DIR, EXIT_FAILURE, EXIT_OK

Status = tuple[str, str]


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def _disabled(reason: str) -> Status:
    return "disabled", reason


def _check_data() -> Status:
    errors = validate_data_dir(DATA_DIR)
    if errors:
        return "misconfigured", "; ".join(errors[:3])
    return "ok", "registry, schema and derived data valid"


def _check_postgres(settings: Settings, probe: bool) -> Status:
    if not settings.persist_to_postgres:
        return _disabled("PERSIST_TO_POSTGRES=false")
    if not probe:
        return "config-ok", "PERSIST_TO_POSTGRES=true (probe skipped)"
    from ..db.engine import create_db_engine

    engine = create_db_engine(settings)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        return "unreachable", f"{type(exc).__name__}: {exc}"
    finally:
        engine.dispose()
    return "ok", "SELECT 1 succeeded"


def _check_graph(settings: Settings, probe: bool) -> Status:
    if not settings.sync_to_neo4j:
        return _disabled("SYNC_TO_NEO4J=false")
    if not _installed("neo4j"):
        return "misconfigured", "run `uv sync --extra graph`"
    if not probe:
        return "config-ok", "SYNC_TO_NEO4J=true (probe skipped)"
    from ..graph import check_graph_connectivity, get_driver

    driver = get_driver(settings)
    try:
        if not check_graph_connectivity(driver):
            return "unreachable", settings.neo4j_uri
    except Exception as exc:
        return "unreachable", f"{type(exc).__name__}: {exc}"
    finally:
        driver.close()
    return "ok", f"connected to {settings.neo4j_uri}"


def _check_llm(settings: Settings) -> Status:
    provider = str(settings.llm_provider).strip().lower()
    translation = str(settings.translation_mode).strip().lower()
    judge = str(settings.ai_judge_mode).strip().lower()
    if provider == "local" and translation == "off" and judge == "off":
        return _disabled("local provider; no live model calls")

    issues: list[str] = []
    if provider != "openrouter":
        issues.append("LLM_PROVIDER must be openrouter for live inference")
    if not settings.openrouter_api_key or not settings.openrouter_api_key.get_secret_value():
        issues.append("OPENROUTER_API_KEY is empty")
    snapshot = str(settings.llm_model_snapshot).strip().lower()
    if not snapshot or snapshot in {"unset", "latest"}:
        issues.append("LLM_MODEL_SNAPSHOT is not pinned")
    if judge != "off":
        judge_snapshot = str(settings.ai_judge_model_snapshot).strip().lower()
        if not judge_snapshot or judge_snapshot in {"unset", "latest"}:
            issues.append("AI_JUDGE_MODEL_SNAPSHOT is not pinned")
    if not _installed("openai"):
        issues.append("run `uv sync --extra llm`")
    if not settings.persist_to_postgres:
        issues.append("PERSIST_TO_POSTGRES=true is required for the decision cache")
    if issues:
        return "misconfigured", "; ".join(issues)
    return "ok", "key, pinned model, openai extra and cache configured (no paid call made)"


def _check_dashboard(settings: Settings) -> Status:
    password = settings.dashboard_password
    if password is not None and password.get_secret_value().strip():
        return "ok", "DASHBOARD_PASSWORD is set"
    return "check", "no DASHBOARD_PASSWORD in .env; Streamlit secrets fallback may supply it"


def _check_otel(settings: Settings) -> Status:
    if not settings.otel_enabled:
        return _disabled("OTEL_ENABLED=false")
    endpoint = str(settings.otel_exporter_otlp_endpoint or "")
    if not endpoint.startswith("http"):
        return "misconfigured", "OTEL_EXPORTER_OTLP_ENDPOINT must be an http(s) URL"
    if not _installed("opentelemetry.exporter.otlp.proto.http.trace_exporter"):
        return "misconfigured", "install the OpenTelemetry OTLP exporter"
    return "ok", f"exporter configured for {endpoint} (no network probe)"


def _check_dagster(settings: Settings) -> Status:
    if not settings.enable_tier1_schedules and not settings.enable_review_sla_sensor:
        return _disabled("ENABLE_* automation flags are false")
    if not _installed("dagster"):
        return "misconfigured", "run `uv sync --extra orchestration`"
    return "ok", "flags set; start existing schedules with scripts/ops/enable_automation.ps1"


def _report(label: str, status: Status) -> tuple[str, bool]:
    state, detail = status
    failed = state in {"unreachable", "misconfigured"}
    return f"{label:<20} {state:<13} {detail}", failed


def _do_preflight(settings: Settings, *, probe: bool = True) -> int:
    """Print one line per feature; return failure only for a real blocker."""
    checks: list[tuple[str, Callable[[], Status]]] = [
        ("data files", _check_data),
        ("postgres", lambda: _check_postgres(settings, probe)),
        ("neo4j", lambda: _check_graph(settings, probe)),
        ("llm / openrouter", lambda: _check_llm(settings)),
        ("dashboard", lambda: _check_dashboard(settings)),
        ("otel", lambda: _check_otel(settings)),
        ("dagster", lambda: _check_dagster(settings)),
    ]
    failed = False
    print("== mq preflight ==")
    for label, check in checks:
        line, is_failed = _report(label, check())
        print(line)
        failed = failed or is_failed
    return EXIT_FAILURE if failed else EXIT_OK
