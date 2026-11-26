"""C4 — Dagster daily-partitioned orchestration wrapper (DEC-04).

The asset delegates domain work to the public ``run_daily_pipeline`` service, so the
CLI and Dagster use exactly the same T0-gated collection, verification, rendering and
audit path. Postgres persistence is opt-in via ``PERSIST_TO_POSTGRES=true`` or via the
asset run config.

Includes:
- Rich Dagster UI metadata (inline Markdown digest & audit previews, per-source coverage
  tables, artifact file links, and numeric trend sparklines).
- Structured compute logging with secret redaction and trace correlation.
- Contract asset checks (`export_schema_valid`, `audit_gates_pass`, `coverage_gaps_disclosed`,
  `t0_rights_boundary_enforced`, `quote_rights_limits_respected`, `gold_set_thresholds_met`).
- Governance & evaluation assets (`gold_set_evaluation`, `retention_readiness_report`).
- Operational sensor (`review_queue_sla_sensor`).
"""

import json
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import dagster as dg
from pydantic import field_validator

from .config import DEFAULT_TOPIC_ID, Settings, get_settings
from .db.resources import DatabaseResourceHandle
from .enums import ACQUISITION_PROVENANCES, AcquisitionProvenance
from .observability import configure_observability, redact_sensitive
from .pipeline import DailyPipelineResult, run_daily_pipeline

PARTITION_START = "2026-10-01"
CODE_VERSION = "c4-daily-pipeline-v2"
SCHEDULE_CRON = "0 6 * * *"
TIER1_SCHEDULE_CRON = "0 */6 * * *"
SCHEDULE_TIMEZONE = "UTC"

_SOURCE_MODES = frozenset({"fixtures", "blobs", "live"})
_LIVE_PROVENANCES = frozenset({"live_feed", "live_bing", "manual_sample"})

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def _clean_slug(val: str | None, default: str) -> str:
    candidate = str(val or default).strip()
    if not _SLUG_RE.match(candidate):
        raise ValueError(f"Invalid topic/gold-set slug: {candidate!r}")
    return candidate


class DailyPipelineConfig(dg.Config):
    """Run config exposed to Dagster; no source rights are changed by this config."""

    dry_run: bool = False
    persist_to_postgres: bool | None = None
    topic_id: str | None = None
    source_mode: str = "fixtures"
    tier: int | None = None

    @field_validator("source_mode")
    @classmethod
    def _validate_source_mode(cls, value: str) -> str:
        if value not in _SOURCE_MODES:
            raise ValueError(
                f"unsupported source_mode {value!r}; expected one of {sorted(_SOURCE_MODES)}"
            )
        return value


class CheckConfig(dg.Config):
    """Topic used by the asset under check; mirrors DailyPipelineConfig.topic_id.

    Dagster does not expose the asset's materialization metadata to a check, so when a
    check is executed on its own it must be given the same topic as the asset run it
    validates. Without it the configured default topic is assumed.
    """

    topic_id: str | None = None


class GoldSetCheckConfig(dg.Config):
    """Gold set whose evaluation report this check validates."""

    gold_set: str = "synthetic-v1"


class GoldSetEvalConfig(dg.Config):
    """Run config for on-demand gold-set evaluation."""

    gold_set: str = "synthetic-v1"
    report_date: str | None = None


@dg.resource
def settings_resource(_: dg.InitResourceContext) -> Settings:
    """Injectable settings resource; avoids hidden module-level configuration calls."""
    return get_settings()


@dg.resource
def database_resource(_: dg.InitResourceContext) -> DatabaseResourceHandle:
    """Database session factory resource with engine disposal owned by the service."""
    from .pipeline import _default_session_context

    return DatabaseResourceHandle(_default_session_context)


def _audit_roots(
    settings: Settings, provenance: AcquisitionProvenance | None = None
) -> tuple[Path, ...]:
    """Audit roots: canonical live, fixtures, then each scoped runs tree."""
    fixture_root = settings.audit_dir / "fixtures"
    if provenance == "fixture":
        return (fixture_root,)
    runs_root = settings.audit_dir / "runs"
    scoped = (
        tuple(sorted(sub for sub in runs_root.iterdir() if sub.is_dir()))
        if runs_root.is_dir()
        else ()
    )
    return (settings.audit_dir, fixture_root, *scoped)


def _audit_candidates(
    settings: Settings,
    report_date: date,
    provenance: AcquisitionProvenance | None = None,
) -> list[tuple[float, Path, str]]:
    """(mtime, path, topic) for every audit of a date across canonical/fixtures/runs."""
    name = f"audit-{report_date.isoformat()}.json"
    found: list[tuple[float, Path, str]] = []
    for root in _audit_roots(settings, provenance):
        if not root.is_dir():
            continue
        candidates = [(root / name, DEFAULT_TOPIC_ID)]
        candidates.extend(
            (sub / name, sub.name)
            for sub in sorted(root.iterdir())
            if sub.is_dir() and sub.name not in {"fixtures", "runs"}
        )
        found.extend(
            (path.stat().st_mtime, path, topic) for path, topic in candidates if path.is_file()
        )
    return found


def _load_audit_report(
    settings: Settings,
    report_date: date,
    topic_id: str | None = None,
    provenance: AcquisitionProvenance | None = None,
) -> dict[str, Any]:
    """Load the newest matching audit across canonical, fixtures and runs trees."""
    safe_topic = _clean_slug(topic_id, settings.active_topic)
    candidates = [
        (mtime, path)
        for mtime, path, topic in _audit_candidates(settings, report_date, provenance)
        if topic == safe_topic
    ]
    if not candidates:
        return {}
    return cast(dict[str, Any], json.loads(max(candidates)[1].read_text(encoding="utf-8")))


def _provenance_from_report(report: dict[str, Any]) -> AcquisitionProvenance:
    """Derive acquisition provenance from the selected audit report."""
    value = report.get("provenance")
    return value if value in _LIVE_PROVENANCES else "fixture"


def _selected_audit(
    settings: Settings, report_date: date, topic_id: str | None
) -> tuple[dict[str, Any], AcquisitionProvenance]:
    """Return the selected audit report and its provenance (live wins over fixtures)."""
    report = _load_audit_report(settings, report_date, topic_id)
    return report, _provenance_from_report(report)


def _store_path(
    settings: Settings,
    report_date: date,
    topic_id: str | None = None,
    provenance: AcquisitionProvenance = "fixture",
    scope: str | None = None,
) -> Path:
    """Resolve the export path for the topic/scope the partition actually ran with."""
    from .processing.export import local_store_path

    safe_topic = _clean_slug(topic_id, settings.active_topic)
    return local_store_path(
        settings.export_dir,
        report_date,
        topic_id=safe_topic,
        provenance=provenance,
        scope=scope,
    )


def _check_topic(
    settings: Settings,
    report_date: date,
    requested: str | None,
    provenance: AcquisitionProvenance | None = None,
) -> str:
    """Pick the topic of the newest audit (including scoped runs) when unspecified."""
    if requested:
        return _clean_slug(requested, settings.active_topic)
    candidates = _audit_candidates(settings, report_date, provenance)
    if candidates:
        return max(candidates)[2]
    return settings.active_topic


def _context_partition_key(context: dg.AssetCheckExecutionContext) -> str | None:
    """Read the partition from any AssetCheckContext variant Dagster may expose."""
    for attribute in ("asset_partition_key", "partition_key"):
        try:
            value = getattr(context, attribute, None)
            if isinstance(value, str) and value:
                return value
        except (AttributeError, KeyError, IndexError, TypeError):
            continue
    try:
        keys = getattr(context, "asset_partition_keys", None)
        if keys:
            return next(iter(keys), None)
    except (AttributeError, KeyError, IndexError, TypeError):
        pass
    return None


def _check_date(
    context: dg.AssetCheckExecutionContext, settings: Settings, topic_id: str | None = None
) -> date:
    del topic_id
    partition_key = _context_partition_key(context)
    if partition_key:
        try:
            return date.fromisoformat(partition_key)
        except ValueError:
            pass
    return settings.report_date


def _build_coverage_table(coverage: Sequence[dict[str, Any]]) -> dg.TableMetadataValue:
    """Render per-source T0 & coverage rows as a rich Dagster UI table."""
    schema = dg.TableSchema(
        columns=[
            dg.TableColumn("source_id", "string", description="Source identifier"),
            dg.TableColumn("status", "string", description="ok | degraded | blocked"),
            dg.TableColumn("mandatory", "bool", description="Tier-1 mandatory source"),
            dg.TableColumn("discovered", "int", description="Discovered articles in window"),
            dg.TableColumn("quotes", "int", description="Verified quotes published"),
            dg.TableColumn("gap_reason", "string", description="T0 or operational gap reason"),
        ]
    )
    records = [
        dg.TableRecord(
            {
                "source_id": str(row.get("source_id", "")),
                "status": str(row.get("status", "")),
                "mandatory": bool(row.get("mandatory", False)),
                "discovered": int(row.get("discovered", 0)),
                "quotes": int(row.get("quotes", 0)),
                "gap_reason": str(row.get("gap_reason") or "none"),
            }
        )
        for row in coverage
    ]
    return dg.MetadataValue.table(records=records, schema=schema)


def _log_pipeline_events(context: dg.AssetExecutionContext, result: DailyPipelineResult) -> None:
    """Emit structured, secret-redacted stage summaries into Dagster compute logs."""
    blocked_mandatory = sorted(
        {str(gap.get("source_id")) for gap in result.gaps if gap.get("mandatory")}
    )
    summary_payload = redact_sensitive(
        None,
        "info",
        {
            "run_id": result.run_id,
            "topic_id": result.topic_id,
            "report_date": result.report_date.isoformat(),
            "status": result.status,
            "quotes_verified": len(result.records),
            "rejected_total": result.rejected_total,
            "coverage_sources": len(result.coverage),
            "coverage_gaps": len(result.gaps),
            "blocked_mandatory": blocked_mandatory,
            "persistence": dict(result.persistence),
        },
    )
    context.log.info(f"Daily pipeline summary: {json.dumps(summary_payload, sort_keys=True)}")
    if blocked_mandatory:
        sources_csv = ", ".join(blocked_mandatory)
        context.log.warning(
            f"T0 rights gate active for mandatory sources: {sources_csv} "
            "(replayed from synthetic fixtures or disclosed as coverage gaps)."
        )


def _observed_retention_months(settings: Settings, database: DatabaseResourceHandle) -> list[date]:
    """Read observed partition months only when persistence is enabled.

    Offline runs report no months so the retention planner cannot fabricate partitions.
    """
    if not settings.persist_to_postgres:
        return []
    from .db.repository import observed_audit_months

    with database.context()(settings) as session:
        return observed_audit_months(session)


def build_definitions() -> dg.Definitions:
    """Build Dagster definitions lazily so importing this module never starts a daemon."""
    runtime_settings = get_settings()
    configure_observability(runtime_settings)  # once per process, not once per asset run
    schedule_status = (
        dg.DefaultScheduleStatus.RUNNING
        if runtime_settings.enable_tier1_schedules
        else dg.DefaultScheduleStatus.STOPPED
    )
    sensor_status = (
        dg.DefaultSensorStatus.RUNNING
        if runtime_settings.enable_review_sla_sensor
        else dg.DefaultSensorStatus.STOPPED
    )
    partitions_def = dg.DailyPartitionsDefinition(
        start_date=PARTITION_START, end_offset=1, timezone=SCHEDULE_TIMEZONE
    )
    job_resource_defs = {"settings": settings_resource, "database": database_resource}

    @dg.asset(
        partitions_def=partitions_def,
        code_version=CODE_VERSION,
        name="daily_quotes_digest",
        group_name="media_quotes",
        owners=["team:Balgove Innovations"],
        tags={"domain": "media_quotes", "mode": "rights-gated"},
        description=(
            "Daily T0-gated quote collection, verification, digest and audit output. "
            "Supports fixture replay and rights-cleared live/blob collection; the source_mode "
            "is chosen per run and the schedules request live collection."
        ),
        retry_policy=dg.RetryPolicy(max_retries=1, delay=2),  # transient live fetch failures only
    )
    def daily_quotes_digest(
        context: dg.AssetExecutionContext,
        config: DailyPipelineConfig,
        settings: dg.ResourceParam[Settings],
        database: dg.ResourceParam[DatabaseResourceHandle],
    ) -> dict[str, Any]:
        app_settings = settings
        report_date = date.fromisoformat(context.partition_key)
        active_topic = _clean_slug(config.topic_id, app_settings.active_topic)
        context.log.info(
            f"Starting daily_quotes_digest for partition {report_date.isoformat()} "
            f"(topic={active_topic}, dry_run={config.dry_run})"
        )
        tier_suffix = f"-t{config.tier}" if config.tier is not None else ""
        result: DailyPipelineResult = run_daily_pipeline(
            app_settings,
            report_date=report_date,
            run_id=(
                f"dagster-{report_date.isoformat()}-{active_topic}-{config.source_mode}"
                f"{tier_suffix}"
            ),
            dry_run=config.dry_run,
            pipeline="dagster-tier1" if config.tier == 1 else "dagster-daily",
            topic_id=config.topic_id,
            persist_to_postgres=config.persist_to_postgres,
            session_context=database.context(),
            source_mode=config.source_mode,
            tier=config.tier,
        )
        _log_pipeline_events(context, result)
        if result.dry_run:
            summary = {
                "run_id": result.run_id,
                "topic_id": result.topic_id,
                "report_date": result.report_date.isoformat(),
                "dry_run": True,
                "quotes": len(result.records),
                "gaps": len(result.gaps),
                "status": "dry-run",
            }
            context.add_output_metadata(summary)
            return summary

        audit_report, _ = _selected_audit(app_settings, report_date, result.topic_id)
        checks: dict[str, Any] = audit_report.get("checks", {})
        digest_md = (
            result.digest_path.read_text(encoding="utf-8")
            if result.digest_path and result.digest_path.is_file()
            else ""
        )
        audit_md = (
            result.audit_md_path.read_text(encoding="utf-8")
            if result.audit_md_path and result.audit_md_path.is_file()
            else ""
        )

        metadata: dict[str, Any] = {
            "run_id": result.run_id,
            "topic_id": result.topic_id,
            "report_date": result.report_date.isoformat(),
            "quotes": len(result.records),
            "coverage": len(result.coverage),
            "gaps": len(result.gaps),
            "rejected": result.rejected_total,
            "status": result.status,
            "gates_pass": bool(checks.get("gates_pass", False)),
            "digest_path": dg.MetadataValue.path(str(result.digest_path or "")),
            "export_path": dg.MetadataValue.path(str(result.store_path or "")),
            "logs_path": dg.MetadataValue.path(str(result.logs_path or "")),
            "audit_json_path": dg.MetadataValue.path(str(result.audit_json_path or "")),
            "audit_md_path": dg.MetadataValue.path(str(result.audit_md_path or "")),
            "coverage_table": _build_coverage_table(result.coverage),
            **checks,
        }
        if digest_md:
            metadata["digest_preview"] = dg.MetadataValue.md(digest_md)
        if audit_md:
            metadata["audit_report_preview"] = dg.MetadataValue.md(audit_md)

        if app_settings.sync_to_neo4j and not config.dry_run and result.records:
            g_driver = None
            try:
                from .graph import get_driver, graph_session, init_schema, project_records_to_graph

                g_driver = get_driver(app_settings)
                with graph_session(g_driver, app_settings.neo4j_database) as g_sess:
                    init_schema(g_sess)
                    g_stats = project_records_to_graph(result.records, g_sess)
                metadata["neo4j_quotes_projected"] = g_stats.quotes_projected
            except Exception as g_err:
                # Enabled sync is mandatory: a projection failure must fail the asset.
                context.log.error(f"Neo4j sync failed: {g_err}")
                metadata["neo4j_sync_error"] = str(g_err)
                raise RuntimeError(f"Neo4j sync failed: {g_err}") from g_err
            finally:
                if g_driver is not None:
                    g_driver.close()

        context.add_output_metadata(metadata)
        return {
            "run_id": result.run_id,
            "topic_id": result.topic_id,
            "report_date": result.report_date.isoformat(),
            "quotes": len(result.records),
            "coverage": len(result.coverage),
            "gaps": len(result.gaps),
            "rejected": result.rejected_total,
            "status": result.status,
            "gates_pass": bool(checks.get("gates_pass", False)),
            "export_path": str(result.store_path or ""),
            "logs_path": str(result.logs_path or ""),
            "audit_json_path": str(result.audit_json_path or ""),
            **checks,
        }

    @dg.asset(
        code_version=CODE_VERSION,
        name="gold_set_evaluation",
        group_name="media_quotes_governance",
        owners=["team:Balgove Innovations"],
        tags={"domain": "media_quotes", "contract": "CHK-03"},
        description=(
            "Deterministic 60/20/20 train/dev/test gold-set evaluation against acceptance "
            "thresholds (0% false quote rate, >=90% recall, >=95% span & metadata accuracy)."
        ),
    )
    def gold_set_evaluation(
        context: dg.AssetExecutionContext,
        config: GoldSetEvalConfig,
        settings: dg.ResourceParam[Settings],
    ) -> dict[str, Any]:
        from .cli.eval import evaluate_gold_set_report

        app_settings = settings
        eval_date = (
            date.fromisoformat(config.report_date)
            if config.report_date
            else app_settings.report_date
        )
        safe_gold_set = _clean_slug(config.gold_set, "synthetic-v1")
        metrics = evaluate_gold_set_report(
            app_settings, gold_set=safe_gold_set, report_date=eval_date
        )
        splits = metrics.get("splits", {})
        version_str = metrics["gold_set_version"]
        date_str = metrics["report_date"]
        splits_str = (
            f"train={splits.get('train', 0)}, "
            f"dev={splits.get('dev', 0)}, "
            f"test={splits.get('test', 0)}"
        )
        md_lines = [
            f"### Gold Set Evaluation (`{version_str}` — `{date_str}`)",
            f"- **Overall Pass:** `{metrics['pass']}`",
            f"- **Splits:** {splits_str}",
            f"- **Article Recall:** `{metrics['article_recall']}`",
            f"- **Quote Recall:** `{metrics['quote_recall']}`",
            f"- **Span Exact Match:** `{metrics['span_exact_match']}`",
            f"- **Metadata Accuracy:** `{metrics['metadata_accuracy']}`",
            f"- **Section Accuracy:** `{metrics['section_accuracy']}`",
            f"- **False Quote Rate:** `{metrics['false_quote_rate']}`",
        ]
        context.log.info(
            f"Gold set {safe_gold_set} evaluated: pass={metrics['pass']}, "
            f"quote_recall={metrics['quote_recall']}, "
            f"false_quote_rate={metrics['false_quote_rate']}"
        )
        context.add_output_metadata(
            {
                "gold_set_version": str(metrics["gold_set_version"]),
                "report_date": str(metrics["report_date"]),
                "passed": bool(metrics["pass"]),
                "article_recall": float(metrics["article_recall"]),
                "quote_recall": float(metrics["quote_recall"]),
                "span_exact_match": float(metrics["span_exact_match"]),
                "metadata_accuracy": float(metrics["metadata_accuracy"]),
                "section_accuracy": float(metrics["section_accuracy"]),
                "false_quote_rate": float(metrics["false_quote_rate"]),
                "eval_report_path": dg.MetadataValue.path(
                    str(app_settings.audit_dir / f"eval-{safe_gold_set}.json")
                ),
                "summary_preview": dg.MetadataValue.md("\n".join(md_lines)),
            }
        )
        return metrics

    @dg.asset(
        code_version=CODE_VERSION,
        name="retention_readiness_report",
        group_name="media_quotes_governance",
        owners=["team:Balgove Innovations"],
        tags={"domain": "media_quotes", "contract": "CHK-17"},
        description=(
            "RSK-11 monthly partition detach readiness, 180-day blob / 90-day log cutoffs, "
            "and transaction lock duration guardrails. Audit rows are never dropped."
        ),
    )
    def retention_readiness_report(
        context: dg.AssetExecutionContext,
        settings: dg.ResourceParam[Settings],
        database: dg.ResourceParam[DatabaseResourceHandle],
    ) -> dict[str, Any]:
        from .governance.restore_drill import retention_readiness
        from .risk_controls.retention import RetentionPolicy

        today = settings.report_date
        months = _observed_retention_months(settings, database)
        report = retention_readiness(RetentionPolicy(), today, months, longest_txn_seconds=0)
        report.pop("longest_txn_ok", None)  # no live measurement exists in offline mode
        report["partition_months_observed"] = settings.persist_to_postgres
        hot_count = len(report["partitions_hot"])
        detach_count = len(report["partitions_to_detach"])
        context.log.info(
            f"Retention readiness check: hot={hot_count}, detach={detach_count}; "
            "partition months observed="
            f"{'yes' if months else 'no (no plan fabricated)'}; lock duration not measured"
        )
        context.add_output_metadata(
            {
                "blob_cutoff": str(report["blob_cutoff"]),
                "log_cutoff": str(report["log_cutoff"]),
                "audit_rows_dropped": bool(report["audit_rows_dropped"]),
                "hot_partition_count": hot_count,
                "detach_partition_count": detach_count,
                "max_index_bloat_ratio": float(report["max_index_bloat_ratio"]),
                "index_bloat_ratio": float(report["index_bloat_ratio"]),
                "index_bloat_ok": bool(report["index_bloat_ok"]),
                "plan_json": dg.MetadataValue.json(report),
            }
        )
        return report

    @dg.asset_check(
        asset=retention_readiness_report,
        name="retention_index_bloat_within_limit",
        description="Verify measured index bloat does not exceed the agreed threshold.",
    )
    def retention_index_bloat_within_limit(
        context: dg.AssetCheckExecutionContext,
        settings: dg.ResourceParam[Settings],
        database: dg.ResourceParam[DatabaseResourceHandle],
    ) -> dg.AssetCheckResult:
        del context
        from .governance.restore_drill import retention_readiness
        from .risk_controls.retention import RetentionPolicy

        today = settings.report_date
        months = _observed_retention_months(settings, database)
        report = retention_readiness(RetentionPolicy(), today, months, longest_txn_seconds=0)
        bloat_ok = bool(report.get("index_bloat_ok", True))
        ratio = float(report.get("index_bloat_ratio", 0.0))
        max_ratio = float(report.get("max_index_bloat_ratio", 0.30))
        return dg.AssetCheckResult(
            passed=bloat_ok,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={"index_bloat_ratio": ratio, "max_index_bloat_ratio": max_ratio},
            description=f"Index bloat ratio {ratio:.2%} exceeds limit {max_ratio:.2%}"
            if not bloat_ok
            else None,
        )

    @dg.asset_check(
        asset=daily_quotes_digest,
        name="export_schema_valid",
        description="The dated JSONL export reads back as strict ExportQuoteRecord values.",
    )
    def export_schema_valid(
        context: dg.AssetCheckExecutionContext,
        config: CheckConfig,
        settings: dg.ResourceParam[Settings],
    ) -> dg.AssetCheckResult:
        from .processing.export import read_jsonl

        app_settings = settings
        report_date = _check_date(context, app_settings, config.topic_id)
        topic = _check_topic(app_settings, report_date, config.topic_id)
        report, provenance = _selected_audit(app_settings, report_date, topic)
        records = read_jsonl(
            _store_path(
                app_settings,
                report_date,
                topic,
                provenance,
                scope=report.get("artifact_scope"),
            )
        )
        return dg.AssetCheckResult(
            passed=bool(records),
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={"quote_count": len(records)},
            description="No strict quote records were found for the partition"
            if not records
            else None,
        )

    @dg.asset_check(
        asset=daily_quotes_digest,
        name="audit_gates_pass",
        description="The deterministic audit report reports gates_pass=true.",
    )
    def audit_gates_pass(
        context: dg.AssetCheckExecutionContext,
        config: CheckConfig,
        settings: dg.ResourceParam[Settings],
    ) -> dg.AssetCheckResult:
        app_settings = settings
        report_date = _check_date(context, app_settings, config.topic_id)
        topic = _check_topic(app_settings, report_date, config.topic_id)
        report, _ = _selected_audit(app_settings, report_date, topic)
        gates_pass = bool(report.get("checks", {}).get("gates_pass", False))
        return dg.AssetCheckResult(
            passed=gates_pass,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={"gates_pass": gates_pass},
            description="Audit report is missing or gates did not pass" if not gates_pass else None,
        )

    @dg.asset_check(
        asset=daily_quotes_digest,
        name="coverage_gaps_disclosed",
        description="Blocked sources are disclosed as coverage gaps rather than silently skipped.",
    )
    def coverage_gaps_disclosed(
        context: dg.AssetCheckExecutionContext,
        config: CheckConfig,
        settings: dg.ResourceParam[Settings],
    ) -> dg.AssetCheckResult:
        app_settings = settings
        report_date = _check_date(context, app_settings, config.topic_id)
        topic = _check_topic(app_settings, report_date, config.topic_id)
        report, _ = _selected_audit(app_settings, report_date, topic)
        if not report:
            return dg.AssetCheckResult(
                passed=False,
                severity=dg.AssetCheckSeverity.ERROR,
                description="Audit report is missing; cannot verify coverage disclosures",
            )
        gaps = report.get("coverage_gaps", [])
        disclosed = bool(report.get("checks", {}).get("coverage_gaps_disclosed"))
        passed = (not gaps) or disclosed
        return dg.AssetCheckResult(
            passed=passed,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={"gaps": len(gaps), "disclosed": disclosed},
            description="Coverage gaps exist but were not disclosed" if not passed else None,
        )

    @dg.asset_check(
        asset=daily_quotes_digest,
        name="t0_rights_boundary_enforced",
        description=(
            "CHK-01: Every exported quote comes from either an enabled, rights-cleared source "
            "or a synthetic fixture replay; zero live fetches occur for blocked sources."
        ),
    )
    def t0_rights_boundary_enforced(
        context: dg.AssetCheckExecutionContext,
        config: CheckConfig,
        settings: dg.ResourceParam[Settings],
    ) -> dg.AssetCheckResult:
        from .cli.common import _fixture_mode, _load_registry
        from .processing.export import read_jsonl

        app_settings = settings
        report_date = _check_date(context, app_settings, config.topic_id)
        topic = _check_topic(app_settings, report_date, config.topic_id)
        report, provenance = _selected_audit(app_settings, report_date, topic)
        path = _store_path(
            app_settings, report_date, topic, provenance, scope=report.get("artifact_scope")
        )
        if not path.is_file():
            return dg.AssetCheckResult(
                passed=False,
                severity=dg.AssetCheckSeverity.ERROR,
                description=f"Store file missing: {path}",
            )
        records = read_jsonl(path)
        if not records:
            return dg.AssetCheckResult(
                passed=False,
                severity=dg.AssetCheckSeverity.ERROR,
                description="Store file contains zero records",
            )
        registry = {row.source_id: row for row in _load_registry(app_settings)}
        violations: list[str] = []
        for record in records:
            row = registry.get(record.source_id)
            if row is None:
                violations.append(f"{record.quote_id}:unknown_source:{record.source_id}")
                continue
            if not row.usable and not _fixture_mode(row, app_settings):
                violations.append(f"{record.quote_id}:t0_violation:{record.source_id}")
        passed = not violations
        return dg.AssetCheckResult(
            passed=passed,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={
                "checked_quotes": len(records),
                "violations": len(violations),
            },
            description=f"T0 rights violations detected: {', '.join(violations[:5])}"
            if violations
            else None,
        )

    @dg.asset_check(
        asset=daily_quotes_digest,
        name="quote_rights_limits_respected",
        description=(
            "DEC-10 / CHK-19: Exported quotes never exceed max_quote_words (<=30 words) "
            "and always carry attribution."
        ),
    )
    def quote_rights_limits_respected(
        context: dg.AssetCheckExecutionContext,
        config: CheckConfig,
        settings: dg.ResourceParam[Settings],
    ) -> dg.AssetCheckResult:
        from .governance.rights_guard import apply_rights_limits
        from .processing.export import read_jsonl

        app_settings = settings
        report_date = _check_date(context, app_settings, config.topic_id)
        topic = _check_topic(app_settings, report_date, config.topic_id)
        report, provenance = _selected_audit(app_settings, report_date, topic)
        path = _store_path(
            app_settings, report_date, topic, provenance, scope=report.get("artifact_scope")
        )
        if not path.is_file():
            return dg.AssetCheckResult(
                passed=False,
                severity=dg.AssetCheckSeverity.ERROR,
                description=f"Store file missing: {path}",
            )
        records = read_jsonl(path)
        if not records:
            return dg.AssetCheckResult(
                passed=False,
                severity=dg.AssetCheckSeverity.ERROR,
                description="Store file contains zero records",
            )
        decision = apply_rights_limits(records, max_quote_words=app_settings.max_quote_words)
        passed = len(decision.rejected) == 0
        return dg.AssetCheckResult(
            passed=passed,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={
                "checked_quotes": len(records),
                "over_limit_or_unattributed": len(decision.rejected),
                "max_quote_words": app_settings.max_quote_words,
            },
            description="One or more exported quotes breached word limit or attribution rules"
            if not passed
            else None,
        )

    @dg.asset_check(
        asset=daily_quotes_digest,
        name="live_export_has_no_fixture_records",
        description=(
            "Live/blob runs never publish fixture-stamped records in the canonical export; "
            "fixture-replay runs report an informational skip."
        ),
    )
    def live_export_has_no_fixture_records(
        context: dg.AssetCheckExecutionContext,
        config: CheckConfig,
        settings: dg.ResourceParam[Settings],
    ) -> dg.AssetCheckResult:
        from .processing.export import read_jsonl

        app_settings = settings
        report_date = _check_date(context, app_settings, config.topic_id)
        topic = _check_topic(app_settings, report_date, config.topic_id)
        report, provenance = _selected_audit(app_settings, report_date, topic)
        source_mode = str(report.get("source_mode", "fixtures"))
        is_live = source_mode in {"live", "blobs"} or provenance != "fixture"
        if not is_live:
            return dg.AssetCheckResult(
                passed=True,
                severity=dg.AssetCheckSeverity.WARN,
                metadata={"source_mode": source_mode, "provenance": provenance},
                description="Fixture replay run; live-export provenance check not applicable.",
            )
        path = _store_path(
            app_settings, report_date, topic, provenance, scope=report.get("artifact_scope")
        )
        if not path.is_file():
            return dg.AssetCheckResult(
                passed=False,
                severity=dg.AssetCheckSeverity.ERROR,
                metadata={"source_mode": source_mode, "provenance": provenance},
                description=f"Canonical live export missing: {path}",
            )
        offenders = [
            str(record.quote_id)
            for record in read_jsonl(path)
            if getattr(record, "provenance", None) == "fixture"
        ]
        return dg.AssetCheckResult(
            passed=not offenders,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={
                "source_mode": source_mode,
                "provenance": provenance,
                "fixture_records": len(offenders),
            },
            description=f"Fixture-stamped records in live export: {', '.join(offenders[:5])}"
            if offenders
            else None,
        )

    @dg.asset_check(
        asset=gold_set_evaluation,
        name="gold_set_thresholds_met",
        description=(
            "CHK-03: Gold set evaluation passes all acceptance thresholds (0% false quotes)."
        ),
    )
    def gold_set_thresholds_met(
        context: dg.AssetCheckExecutionContext,
        config: GoldSetCheckConfig,
        settings: dg.ResourceParam[Settings],
    ) -> dg.AssetCheckResult:
        del context
        safe_gold_set = _clean_slug(config.gold_set, "synthetic-v1")
        path = settings.audit_dir / f"eval-{safe_gold_set}.json"
        if not path.is_file():
            return dg.AssetCheckResult(
                passed=False,
                severity=dg.AssetCheckSeverity.WARN,
                description="Gold set evaluation report not yet materialized",
            )
        payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        passed = bool(payload.get("pass", False))
        failed_checks = payload.get("failed_checks", [])
        return dg.AssetCheckResult(
            passed=passed,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={
                "passed": passed,
                "false_quote_rate": float(payload.get("false_quote_rate", 1.0)),
                "quote_recall": float(payload.get("quote_recall", 0.0)),
                "failed_checks": ", ".join(failed_checks) if failed_checks else "none",
            },
            description=f"Failed thresholds: {', '.join(failed_checks)}" if not passed else None,
        )

    job = dg.define_asset_job(
        name="daily_quotes_digest_job",
        selection=[daily_quotes_digest],
        tags={"domain": "media_quotes"},
    )

    governance_job = dg.define_asset_job(
        name="governance_audit_job",
        selection=[gold_set_evaluation, retention_readiness_report],
        tags={"domain": "media_quotes"},
    )

    @dg.schedule(
        name="daily_digest_schedule",
        job=job,
        cron_schedule=SCHEDULE_CRON,
        execution_timezone=SCHEDULE_TIMEZONE,
        default_status=schedule_status,
    )
    def daily_digest_schedule(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
        scheduled = getattr(context, "scheduled_execution_time", None)
        partition = (
            scheduled.date().isoformat()
            if scheduled is not None
            else datetime.now(UTC).date().isoformat()
        )
        return dg.RunRequest(
            run_key=partition,
            partition_key=partition,
            run_config=dg.RunConfig(
                ops={"daily_quotes_digest": DailyPipelineConfig(source_mode="live")}
            ),
        )

    @dg.schedule(
        name="tier1_six_hourly_schedule",
        job=job,
        cron_schedule=TIER1_SCHEDULE_CRON,
        execution_timezone=SCHEDULE_TIMEZONE,
        default_status=schedule_status,
    )
    def tier1_six_hourly_schedule(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
        scheduled = getattr(context, "scheduled_execution_time", None)
        now_dt = scheduled if scheduled is not None else datetime.now(UTC)
        partition = now_dt.date().isoformat()
        run_key = f"{partition}-tier1-{now_dt.hour:02d}"
        return dg.RunRequest(
            run_key=run_key,
            partition_key=partition,
            run_config=dg.RunConfig(
                ops={"daily_quotes_digest": DailyPipelineConfig(tier=1, source_mode="live")}
            ),
        )

    @dg.sensor(
        name="review_queue_sla_sensor",
        minimum_interval_seconds=300,
        default_status=sensor_status,
        description=(
            "RSK-07 / CHK-15: Monitors each acquisition-provenance obsolescence review queue "
            "for pending flags and 1-working-day SLA breaches without leaking "
            "license_reference."
        ),
    )
    def review_queue_sla_sensor(context: dg.SensorEvaluationContext) -> dg.SensorResult:
        from .cli.common import load_review_flags
        from .risk_controls.obsolescence import ReviewQueue

        # Use the settings captured when definitions were built; do not re-read the
        # environment at sensor execution time.
        app_settings = runtime_settings
        now = datetime.now(UTC)
        previous_queues: dict[str, Any] = {}
        if context.cursor:
            try:
                previous_queues = json.loads(context.cursor).get("queues", {})
            except (json.JSONDecodeError, AttributeError, TypeError):
                previous_queues = {}
        queues: dict[str, Any] = {}
        all_pending: set[str] = set()
        all_overdue: set[str] = set()
        alerts: list[str] = []
        for raw_provenance in ACQUISITION_PROVENANCES:
            provenance = cast(AcquisitionProvenance, raw_provenance)
            flags = load_review_flags(app_settings, provenance)
            # One ReviewQueue per provenance: duplicate IDs across queues must not collide.
            overdue = ReviewQueue(flags).overdue(now=now, sla_days=1)
            pending = sorted(
                flag.evidence.old_quote_id
                for flag in flags
                if flag.state in {"pending", "needs_evidence", "escalated"}
            )
            queues[provenance] = {
                "pending_count": len(pending),
                "overdue_count": len(overdue),
                "overdue_quote_ids": overdue,
            }
            all_pending.update(pending)
            all_overdue.update(overdue)
            previously_overdue = set(
                (previous_queues.get(provenance) or {}).get("overdue_quote_ids", [])
            )
            if overdue and set(overdue) != previously_overdue:
                alerts.append(f"{provenance}: {', '.join(overdue)}")
        cursor_payload = {
            "checked_at": now.isoformat(),
            "queues": queues,
            "pending_count": len(all_pending),
            "overdue_count": len(all_overdue),
            "overdue_quote_ids": sorted(all_overdue),
        }
        context.update_cursor(json.dumps(cursor_payload, sort_keys=True))
        if alerts:
            details = "; ".join(alerts)
            context.log.warning(
                f"RSK-07 SLA breach: {len(all_overdue)} obsolescence review flag(s) overdue "
                f">24h by provenance: {details}"
            )
            return dg.SensorResult(
                skip_reason=dg.SkipReason(
                    f"ALERT: {len(all_overdue)} overdue review item(s): "
                    f"{', '.join(sorted(all_overdue))} (by provenance: {details})"
                )
            )
        if all_overdue:
            return dg.SensorResult(skip_reason=dg.SkipReason("Review SLA alert already sent."))
        return dg.SensorResult(
            skip_reason=dg.SkipReason(
                f"Review queue healthy ({len(all_pending)} pending, 0 overdue)."
            )
        )

    return dg.Definitions(
        assets=[daily_quotes_digest, gold_set_evaluation, retention_readiness_report],
        asset_checks=[
            export_schema_valid,
            audit_gates_pass,
            coverage_gaps_disclosed,
            t0_rights_boundary_enforced,
            quote_rights_limits_respected,
            live_export_has_no_fixture_records,
            gold_set_thresholds_met,
            retention_index_bloat_within_limit,
        ],
        jobs=[job, governance_job],
        schedules=[daily_digest_schedule, tier1_six_hourly_schedule],
        sensors=[review_queue_sla_sensor],
        resources=job_resource_defs,
    )


_defs: dg.Definitions | None = None


def __getattr__(name: str) -> Any:
    """Build Dagster definitions only when a caller actually asks for ``defs``."""
    if name != "defs":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    global _defs
    if _defs is None:
        _defs = build_definitions()
    return _defs
