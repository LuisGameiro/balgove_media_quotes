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
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, cast

import dagster as dg

from .config import Settings, get_settings
from .db.resources import DatabaseResourceHandle
from .observability import redact_sensitive
from .pipeline import DailyPipelineResult, run_daily_pipeline

PARTITION_START = "2026-10-01"
CODE_VERSION = "c4-daily-pipeline-v2"
SCHEDULE_CRON = "0 6 * * *"
SCHEDULE_TIMEZONE = "UTC"


class DailyPipelineConfig(dg.Config):
    """Run config exposed to Dagster; no source rights are changed by this config."""

    dry_run: bool = False
    persist_to_postgres: bool | None = None
    topic_id: str | None = None


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


def _load_audit_report(
    settings: Settings, report_date: date, topic_id: str | None = None
) -> dict[str, Any]:
    name = f"audit-{report_date.isoformat()}.json"
    from .cli.audit import _audit_target_dir

    target = _audit_target_dir(settings, str(topic_id or settings.active_topic)) / name
    if target.is_file():
        return cast(dict[str, Any], json.loads(target.read_text(encoding="utf-8")))
    return {}


def _store_path(settings: Settings, report_date: date, topic_id: str | None = None) -> Any:
    """Resolve the export path for the topic the partition actually ran with."""
    from .export import local_store_path

    return local_store_path(
        settings.export_dir, report_date, topic_id=topic_id or settings.active_topic
    )


def _check_date(context: dg.AssetCheckExecutionContext, settings: Settings) -> date:
    partition_key = getattr(context, "asset_partition_key", None)
    return date.fromisoformat(partition_key) if partition_key else settings.report_date


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


def build_definitions() -> dg.Definitions:
    """Build Dagster definitions lazily so importing this module never starts a daemon."""
    partitions_def = dg.DailyPartitionsDefinition(start_date=PARTITION_START)
    job_resource_defs = {"settings": settings_resource, "database": database_resource}

    @dg.asset(
        partitions_def=partitions_def,
        code_version=CODE_VERSION,
        name="daily_quotes_digest",
        group_name="media_quotes",
        owners=["team:Balgove Innovations"],
        tags={"domain": "media_quotes", "mode": "blocked-degraded"},
        description=(
            "Daily T0-gated quote collection, verification, digest and audit output. "
            "Reuters/BBC remain blocked until written rights are recorded."
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
        active_topic = config.topic_id or app_settings.active_topic
        context.log.info(
            f"Starting daily_quotes_digest for partition {report_date.isoformat()} "
            f"(topic={active_topic}, dry_run={config.dry_run})"
        )
        result: DailyPipelineResult = run_daily_pipeline(
            app_settings,
            report_date=report_date,
            run_id=f"dagster-{report_date.isoformat()}-{active_topic}",
            dry_run=config.dry_run,
            pipeline="dagster-daily",
            topic_id=config.topic_id,
            persist_to_postgres=config.persist_to_postgres,
            session_context=database.context(),
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

        checks: dict[str, Any] = dict(result.checks)
        checks.update(result.persistence)

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
        metrics = evaluate_gold_set_report(
            app_settings, gold_set=config.gold_set, report_date=eval_date
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
            f"Gold set {config.gold_set} evaluated: pass={metrics['pass']}, "
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
                    str(app_settings.audit_dir / f"eval-{config.gold_set}.json")
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
    ) -> dict[str, Any]:
        from .restore_drill import retention_readiness
        from .risk_controls.retention import RetentionPolicy

        today = settings.report_date
        months = [date(today.year - 1, 1, 1), date(today.year, today.month, 1)]
        report = retention_readiness(RetentionPolicy(), today, months, longest_txn_seconds=0)
        report.pop("longest_txn_ok", None)  # no live measurement exists in offline mode
        hot_count = len(report["partitions_hot"])
        detach_count = len(report["partitions_to_detach"])
        context.log.info(
            f"Retention readiness check: hot={hot_count}, detach={detach_count}; "
            "lock duration not measured"
        )
        context.add_output_metadata(
            {
                "blob_cutoff": str(report["blob_cutoff"]),
                "log_cutoff": str(report["log_cutoff"]),
                "audit_rows_dropped": bool(report["audit_rows_dropped"]),
                "hot_partition_count": hot_count,
                "detach_partition_count": detach_count,
                "plan_json": dg.MetadataValue.json(report),
            }
        )
        return report

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
        from .export import read_jsonl

        app_settings = settings
        report_date = _check_date(context, app_settings)
        records = read_jsonl(_store_path(app_settings, report_date, config.topic_id))
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
        report = _load_audit_report(
            app_settings, _check_date(context, app_settings), config.topic_id
        )
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
        report = _load_audit_report(
            app_settings, _check_date(context, app_settings), config.topic_id
        )
        gaps = report.get("coverage_gaps", [])
        disclosed = bool(report) and bool(report.get("checks", {}).get("coverage_gaps_disclosed"))
        passed = not gaps or disclosed
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
        from .export import read_jsonl

        app_settings = settings
        report_date = _check_date(context, app_settings)
        records = read_jsonl(_store_path(app_settings, report_date, config.topic_id))
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
        from .export import read_jsonl
        from .rights_guard import apply_rights_limits

        app_settings = settings
        report_date = _check_date(context, app_settings)
        records = read_jsonl(_store_path(app_settings, report_date, config.topic_id))
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
        path = settings.audit_dir / f"eval-{config.gold_set}.json"
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
    )

    governance_job = dg.define_asset_job(
        name="governance_audit_job",
        selection=[gold_set_evaluation, retention_readiness_report],
    )

    @dg.schedule(
        name="daily_digest_schedule",
        job=job,
        cron_schedule=SCHEDULE_CRON,
        execution_timezone=SCHEDULE_TIMEZONE,
        default_status=dg.DefaultScheduleStatus.STOPPED,
    )
    def daily_digest_schedule(context: dg.ScheduleEvaluationContext) -> dg.RunRequest:
        scheduled = context.scheduled_execution_time
        partition = (
            scheduled.date().isoformat()
            if scheduled is not None
            else context.execution_time.date().isoformat()
        )
        return dg.RunRequest(run_key=partition, partition_key=partition)

    @dg.sensor(
        name="review_queue_sla_sensor",
        minimum_interval_seconds=300,
        default_status=dg.DefaultSensorStatus.STOPPED,
        description=(
            "RSK-07 / CHK-15: Monitors the human-in-the-loop obsolescence review queue for "
            "pending flags and 1-working-day SLA breaches without leaking license_reference."
        ),
    )
    def review_queue_sla_sensor(context: dg.SensorEvaluationContext) -> dg.SensorResult:
        from .cli.common import load_review_flags
        from .risk_controls.obsolescence import ReviewQueue

        app_settings = get_settings()
        flags = load_review_flags(app_settings)
        queue = ReviewQueue(flags)
        now = datetime.now(UTC)
        overdue_ids = queue.overdue(now=now, sla_days=1)
        pending_ids = sorted(
            flag.evidence.old_quote_id
            for flag in flags
            if flag.state in {"pending", "needs_evidence", "escalated"}
        )
        cursor_payload = {
            "checked_at": now.isoformat(),
            "pending_count": len(pending_ids),
            "overdue_count": len(overdue_ids),
            "overdue_quote_ids": overdue_ids,
        }
        context.update_cursor(json.dumps(cursor_payload, sort_keys=True))
        if overdue_ids:
            context.log.warning(
                f"RSK-07 SLA breach: {len(overdue_ids)} obsolescence review flag(s) overdue >24h: "
                f"{', '.join(overdue_ids)}"
            )
            return dg.SensorResult(
                skip_reason=dg.SkipReason(
                    f"ALERT: {len(overdue_ids)} overdue review item(s): {', '.join(overdue_ids)}"
                )
            )
        return dg.SensorResult(
            skip_reason=dg.SkipReason(
                f"Review queue healthy ({len(pending_ids)} pending, 0 overdue)."
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
            gold_set_thresholds_met,
        ],
        jobs=[job, governance_job],
        schedules=[daily_digest_schedule],
        sensors=[review_queue_sla_sensor],
        resources=job_resource_defs,
    )


defs: dg.Definitions = build_definitions()
