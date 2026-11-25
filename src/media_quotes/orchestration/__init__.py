"""Dagster orchestration façade.

Implementations live in :mod:`.common`, :mod:`.backfill_range`, :mod:`.checks` and
:mod:`.definitions`. The import path ``media_quotes.orchestration`` and its module-level
``defs`` stay stable for ``dagster dev -m media_quotes.orchestration``. Private names
re-exported here are kept for compatibility with tests that import them directly.
"""

import dagster as dg

from ..backfill import execute_backfill as execute_backfill
from ..config import get_settings
from ..graph import Neo4jResourceHandle
from ..observability import configure_observability
from ..pipeline import run_daily_pipeline
from .backfill_range import _run_backfill_range, _sync_backfill_graph
from .checks import build_asset_checks
from .common import (
    CODE_VERSION,
    GRAPH_CODE_VERSION,
    PARTITION_START,
    SCHEDULE_CRON,
    SCHEDULE_TIMEZONE,
    TIER1_SCHEDULE_CRON,
    CheckConfig,
    DailyPipelineConfig,
    GoldSetCheckConfig,
    GoldSetEvalConfig,
    _backfillable_asset,
    _check_dates,
    _check_settings,
    _check_topic,
    _clean_slug,
    _compat,
    _context_partition_keys,
    _context_partition_range,
    _graph_check_target,
    _graph_export_quotes,
    _graph_projected_quotes,
    _graph_skip_reason,
    _graph_stale_quotes,
    _load_audit_report,
    _store_path,
    database_resource,
    neo4j_resource,
    settings_resource,
)
from .definitions import _build_coverage_table, _log_pipeline_events, build_definitions

__all__ = [
    "CODE_VERSION",
    "GRAPH_CODE_VERSION",
    "PARTITION_START",
    "SCHEDULE_CRON",
    "SCHEDULE_TIMEZONE",
    "TIER1_SCHEDULE_CRON",
    "CheckConfig",
    "DailyPipelineConfig",
    "GoldSetCheckConfig",
    "GoldSetEvalConfig",
    "Neo4jResourceHandle",
    "_backfillable_asset",
    "_build_coverage_table",
    "_check_dates",
    "_check_settings",
    "_check_topic",
    "_clean_slug",
    "_compat",
    "_context_partition_keys",
    "_context_partition_range",
    "_graph_check_target",
    "_graph_export_quotes",
    "_graph_projected_quotes",
    "_graph_skip_reason",
    "_graph_stale_quotes",
    "_load_audit_report",
    "_log_pipeline_events",
    "_run_backfill_range",
    "_store_path",
    "_sync_backfill_graph",
    "build_asset_checks",
    "build_definitions",
    "database_resource",
    "defs",
    "execute_backfill",
    "get_settings",
    "neo4j_resource",
    "run_daily_pipeline",
    "settings_resource",
]

configure_observability(get_settings())  # once per process, not once per asset run

defs: dg.Definitions = build_definitions()
