"""CLI guidance for the optional Dagster orchestration path."""

from __future__ import annotations

import importlib.util
import sys

from ..config import Settings
from .common import EXIT_FAILURE, EXIT_OK


def _do_dagster(settings: Settings) -> int:  # pragma: no cover - operator guidance
    if importlib.util.find_spec("dagster") is None:
        print(
            "Dagster is not installed. Install the extra with: "
            "uv sync --frozen --group dev --extra orchestration",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    from ..orchestration import CODE_VERSION, PARTITION_START

    print("Dagster assets: daily_quotes_digest, gold_set_evaluation, retention_readiness_report")
    print(
        "Asset checks: export_schema_valid, audit_gates_pass, coverage_gaps_disclosed, "
        "t0_rights_boundary_enforced, quote_rights_limits_respected, gold_set_thresholds_met"
    )
    print(f"Partitions: daily from {PARTITION_START}")
    print(f"Code version: {CODE_VERSION}")
    print("Jobs: daily_quotes_digest_job, governance_audit_job")
    print("Schedule: daily_digest_schedule (06:00, stopped by default)")
    print("Sensor: review_queue_sla_sensor (300s interval, stopped by default)")
    print("Start UI: powershell -ExecutionPolicy Bypass -File .\\scripts\\run_dagster.ps1")
    print(f"Persist to Postgres: PERSIST_TO_POSTGRES=true (current={settings.persist_to_postgres})")
    return EXIT_OK
