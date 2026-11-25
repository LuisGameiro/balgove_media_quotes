"""Factories that build Dagster asset checks from the declared asset objects."""

import json
from typing import Any, cast

import dagster as dg

from ..config import Settings
from ..graph import Neo4jResourceHandle
from .common import (
    CheckConfig,
    GoldSetCheckConfig,
    _check_dates,
    _check_settings,
    _check_topic,
    _clean_slug,
    _graph_check_target,
    _graph_export_quotes,
    _graph_projected_quotes,
    _graph_stale_quotes,
    _load_audit_report,
    _store_path,
)


def build_asset_checks(
    daily_quotes_digest: dg.AssetsDefinition,
    neo4j_quote_projection: dg.AssetsDefinition,
    gold_set_evaluation: dg.AssetsDefinition,
) -> list[Any]:
    """Declare every contract check against the assets built by ``build_definitions``."""

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
        from ..export import read_jsonl

        dates = _check_dates(context, settings)
        app_settings = _check_settings(settings, config, dates)
        topic = _check_topic(app_settings, dates[0], config.topic_id)
        counts = {
            report_date.isoformat(): len(read_jsonl(_store_path(app_settings, report_date, topic)))
            for report_date in dates
        }
        passed = all(count > 0 for count in counts.values())
        return dg.AssetCheckResult(
            passed=passed,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={"quote_count": sum(counts.values()), "partitions": counts},
            description="No strict quote records were found for one or more partitions"
            if not passed
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
        dates = _check_dates(context, settings)
        app_settings = _check_settings(settings, config, dates)
        topic = _check_topic(app_settings, dates[0], config.topic_id)
        results = {
            report_date.isoformat(): bool(
                _load_audit_report(app_settings, report_date, topic)
                .get("checks", {})
                .get("gates_pass", False)
            )
            for report_date in dates
        }
        passed = all(results.values())
        return dg.AssetCheckResult(
            passed=passed,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={"gates_pass": passed, "partitions": results},
            description="Audit report is missing or gates did not pass for one or more partitions"
            if not passed
            else None,
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
        dates = _check_dates(context, settings)
        app_settings = _check_settings(settings, config, dates)
        topic = _check_topic(app_settings, dates[0], config.topic_id)
        total_gaps = 0
        missing: list[str] = []
        passed = True
        for report_date in dates:
            report = _load_audit_report(app_settings, report_date, topic)
            if not report:
                missing.append(report_date.isoformat())
                passed = False
                continue
            gaps = report.get("coverage_gaps", [])
            disclosed = bool(report.get("checks", {}).get("coverage_gaps_disclosed"))
            total_gaps += len(gaps)
            passed = passed and ((not gaps) or disclosed)
        return dg.AssetCheckResult(
            passed=passed,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={"gaps": total_gaps, "missing_partitions": missing},
            description="Coverage gaps are undisclosed or an audit report is missing"
            if not passed
            else None,
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
        from ..cli.common import _fixture_mode, _load_registry
        from ..export import read_jsonl

        dates = _check_dates(context, settings)
        app_settings = _check_settings(settings, config, dates)
        topic = _check_topic(app_settings, dates[0], config.topic_id)
        registry = {row.source_id: row for row in _load_registry(app_settings)}
        violations: list[str] = []
        checked = 0
        for report_date in dates:
            path = _store_path(app_settings, report_date, topic)
            if not path.is_file():
                violations.append(f"{report_date}:missing_store")
                continue
            records = read_jsonl(path)
            if not records:
                violations.append(f"{report_date}:empty_store")
                continue
            checked += len(records)
            for record in records:
                row = registry.get(record.source_id)
                if row is None:
                    violations.append(f"{record.quote_id}:unknown_source:{record.source_id}")
                    continue
                if not row.usable and not _fixture_mode(row, app_settings):
                    violations.append(f"{record.quote_id}:t0_violation:{record.source_id}")
        return dg.AssetCheckResult(
            passed=not violations,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={
                "checked_quotes": checked,
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
        from ..export import read_jsonl
        from ..rights_guard import apply_rights_limits

        dates = _check_dates(context, settings)
        app_settings = _check_settings(settings, config, dates)
        topic = _check_topic(app_settings, dates[0], config.topic_id)
        checked = 0
        rejected = 0
        missing: list[str] = []
        for report_date in dates:
            path = _store_path(app_settings, report_date, topic)
            if not path.is_file():
                missing.append(report_date.isoformat())
                continue
            records = read_jsonl(path)
            if not records:
                missing.append(report_date.isoformat())
                continue
            checked += len(records)
            decision = apply_rights_limits(records, max_quote_words=app_settings.max_quote_words)
            rejected += len(decision.rejected)
        passed = not missing and rejected == 0
        return dg.AssetCheckResult(
            passed=passed,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={
                "checked_quotes": checked,
                "over_limit_or_unattributed": rejected,
                "missing_partitions": missing,
                "max_quote_words": app_settings.max_quote_words,
            },
            description=(
                "One or more exported quotes breached rights limits or a partition is missing"
            )
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

    @dg.asset_check(
        asset=neo4j_quote_projection,
        name="neo4j_projection_no_errors",
        description="The Neo4j projection metadata reports no per-record errors.",
    )
    def neo4j_projection_no_errors(
        context: dg.AssetCheckExecutionContext,
        config: CheckConfig,
        settings: dg.ResourceParam[Settings],
        neo4j: dg.ResourceParam[Neo4jResourceHandle],
    ) -> dg.AssetCheckResult:
        from ..graph import fetch_sync_metadata, graph_session

        dates, app_settings, topic, skipped = _graph_check_target(context, settings, config)
        if skipped:
            return dg.AssetCheckResult(passed=True, metadata={"skipped": True})
        expected, missing = _graph_export_quotes(app_settings, dates, topic)
        if missing:
            return dg.AssetCheckResult(
                passed=False,
                severity=dg.AssetCheckSeverity.ERROR,
                metadata={"missing_exports": missing},
                description="Expected export file is missing",
            )
        if not expected:
            return dg.AssetCheckResult(passed=True, metadata={"export_quotes": 0})
        with graph_session(neo4j.get_driver(), app_settings.neo4j_database) as session:
            metadata = fetch_sync_metadata(session)
        error_count = int(metadata.get("error_count", 1))
        return dg.AssetCheckResult(
            passed=error_count == 0,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={"error_count": error_count},
        )

    @dg.asset_check(
        asset=neo4j_quote_projection,
        name="neo4j_export_quotes_present",
        description=(
            "Every quote in the upstream export exists in the Neo4j projection for its topic "
            "with the same content hash."
        ),
    )
    def neo4j_export_quotes_present(
        context: dg.AssetCheckExecutionContext,
        config: CheckConfig,
        settings: dg.ResourceParam[Settings],
        neo4j: dg.ResourceParam[Neo4jResourceHandle],
    ) -> dg.AssetCheckResult:
        from ..graph import graph_session

        dates, app_settings, topic, skipped = _graph_check_target(context, settings, config)
        if skipped:
            return dg.AssetCheckResult(passed=True, metadata={"skipped": True})
        expected, missing = _graph_export_quotes(app_settings, dates, topic)
        if missing:
            return dg.AssetCheckResult(
                passed=False,
                severity=dg.AssetCheckSeverity.ERROR,
                metadata={"missing_exports": missing},
                description="Expected export file is missing",
            )
        if not expected:
            return dg.AssetCheckResult(passed=True, metadata={"export_quotes": 0})
        with graph_session(neo4j.get_driver(), app_settings.neo4j_database) as session:
            projected = _graph_projected_quotes(session, list(expected), topic)
        mismatched = _graph_stale_quotes(expected, projected, check_content=True)
        return dg.AssetCheckResult(
            passed=not mismatched,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={
                "export_quotes": len(expected),
                "graph_quotes": len(projected),
                "stale_or_changed_quotes": mismatched,
            },
        )

    @dg.asset_check(
        asset=neo4j_quote_projection,
        name="neo4j_projection_metadata_current",
        description="Every expected quote was projected after the checked partition started.",
    )
    def neo4j_projection_metadata_current(
        context: dg.AssetCheckExecutionContext,
        config: CheckConfig,
        settings: dg.ResourceParam[Settings],
        neo4j: dg.ResourceParam[Neo4jResourceHandle],
    ) -> dg.AssetCheckResult:
        from ..graph import graph_session

        dates, app_settings, topic, skipped = _graph_check_target(context, settings, config)
        if skipped:
            return dg.AssetCheckResult(passed=True, metadata={"skipped": True})
        expected, missing = _graph_export_quotes(app_settings, dates, topic)
        if missing:
            return dg.AssetCheckResult(
                passed=False,
                severity=dg.AssetCheckSeverity.ERROR,
                metadata={"missing_exports": missing},
                description="Expected export file is missing",
            )
        if not expected:
            return dg.AssetCheckResult(passed=True, metadata={"export_quotes": 0})
        with graph_session(neo4j.get_driver(), app_settings.neo4j_database) as session:
            projected = _graph_projected_quotes(session, list(expected), topic)
        stale = _graph_stale_quotes(expected, projected)
        return dg.AssetCheckResult(
            passed=not stale,
            severity=dg.AssetCheckSeverity.ERROR,
            metadata={"stale_quotes": stale},
        )

    return [
        export_schema_valid,
        audit_gates_pass,
        coverage_gaps_disclosed,
        t0_rights_boundary_enforced,
        quote_rights_limits_respected,
        neo4j_projection_no_errors,
        neo4j_export_quotes_present,
        neo4j_projection_metadata_current,
        gold_set_thresholds_met,
    ]
