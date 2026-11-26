"""CLI guidance for the optional Dagster orchestration path."""

from __future__ import annotations

import importlib.util
import sys

from ..config import Settings
from .common import EXIT_FAILURE, EXIT_OK


def _do_dagster(settings: Settings) -> int:  # pragma: no cover - operator guidance
    if importlib.util.find_spec("dagster") is None:
        print(
            "Dagster is not installed. Install the extra with: uv sync --frozen --all-extras",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    from ..orchestration import CODE_VERSION, PARTITION_START

    print(
        "Dagster assets: daily_quotes_digest, neo4j_quote_projection, gold_set_evaluation, "
        "retention_readiness_report"
    )
    print(
        "Asset checks: export_schema_valid, audit_gates_pass, coverage_gaps_disclosed, "
        "t0_rights_boundary_enforced, quote_rights_limits_respected, "
        "neo4j_projection_no_errors, neo4j_export_quotes_present, "
        "neo4j_projection_metadata_current, gold_set_thresholds_met"
    )
    print(f"Partitions: daily from {PARTITION_START}")
    print(f"Code version: {CODE_VERSION}")
    print("Jobs: daily_quotes_digest_job, governance_audit_job")
    print(
        "Schedules: daily_digest_schedule (06:00) and tier1_six_hourly_schedule (tier 1, every "
        "6h); both stopped by default and explicitly live"
    )
    print("Fixture default: artifacts live under var/output/fixtures; explicit config is required")
    print("Sensor: review_queue_sla_sensor (300s interval, stopped by default)")
    print("Start UI: uv run dagster dev -m media_quotes.orchestration")
    print("Set DAGSTER_HOME=var/dagster to use var/dagster/dagster.yaml")
    print(
        "Start UI (Windows): powershell -ExecutionPolicy Bypass -File .\\scripts\\run_dagster.ps1"
    )
    print(f"Persist to Postgres: PERSIST_TO_POSTGRES=true (current={settings.persist_to_postgres})")
    return EXIT_OK
