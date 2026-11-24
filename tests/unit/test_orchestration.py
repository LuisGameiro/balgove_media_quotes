"""Dagster definitions and in-process materialization contract."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("dagster")

import media_quotes.orchestration as orchestration
from media_quotes.config import Settings
from media_quotes.orchestration import (
    CODE_VERSION,
    PARTITION_START,
    SCHEDULE_CRON,
    SCHEDULE_TIMEZONE,
    build_definitions,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_jobs_carry_concurrency_tag() -> None:
    defs = build_definitions()
    for name in ("daily_quotes_digest_job", "governance_audit_job"):
        assert defs.resolve_job_def(name).run_tags.get("domain") == "media_quotes"


def _check_names(defs: Any) -> list[str]:
    groups = defs.asset_checks
    return sorted(str(key.name) for group in groups for key in getattr(group, "check_keys", set()))


def _make_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        persist_to_postgres=False,
        report_date=date(2026, 11, 19),
        digest_dir=tmp_path / "digests",
        export_dir=tmp_path / "export",
        logs_dir=tmp_path / "logs",
        audit_dir=tmp_path / "audit",
        sources_csv=REPO_ROOT / "data" / "sources.csv",
        sections_csv=REPO_ROOT / "data" / "sections.csv",
        schema_path=REPO_ROOT / "data" / "quotes.schema.json",
        fixtures_dir=REPO_ROOT / "data" / "fixtures",
        gold_set_dir=REPO_ROOT / "data" / "gold_set",
    )


def test_definitions_expose_asset_job_checks_and_schedule() -> None:
    defs = build_definitions()
    assert sorted(asset.key.to_user_string() for asset in defs.assets) == [
        "daily_quotes_digest",
        "gold_set_evaluation",
        "retention_readiness_report",
    ]
    assert sorted(job.name for job in defs.jobs) == [
        "daily_quotes_digest_job",
        "governance_audit_job",
    ]
    assert [schedule.name for schedule in defs.schedules] == [
        "daily_digest_schedule",
        "tier1_six_hourly_schedule",
    ]
    assert [sensor.name for sensor in defs.sensors] == ["review_queue_sla_sensor"]
    assert _check_names(defs) == [
        "audit_gates_pass",
        "coverage_gaps_disclosed",
        "export_schema_valid",
        "gold_set_thresholds_met",
        "quote_rights_limits_respected",
        "t0_rights_boundary_enforced",
    ]
    assert PARTITION_START == "2026-10-01"
    assert CODE_VERSION == "c4-daily-pipeline-v2"


def test_schedule_runs_daily_at_0600_utc() -> None:
    defs = build_definitions()
    schedule = defs.schedules[0]
    assert schedule.cron_schedule == SCHEDULE_CRON == "0 6 * * *"
    assert schedule.execution_timezone == SCHEDULE_TIMEZONE == "UTC"


def test_asset_has_governance_metadata() -> None:
    defs = build_definitions()
    spec = next(iter(defs.assets[0].specs))
    assert spec.group_name == "media_quotes"
    assert spec.code_version == CODE_VERSION
    assert spec.tags.get("domain") == "media_quotes"


def test_schedule_emits_todays_partition_at_0600() -> None:
    import dagster as dg

    defs = build_definitions()
    scheduled = datetime(2026, 11, 20, 6, tzinfo=UTC)
    context = dg.build_schedule_context(
        repository_def=defs.get_repository_def(),
        scheduled_execution_time=scheduled,
    )
    request = defs.schedules[0]._execution_fn.decorated_fn(context)
    assert request is not None
    assert request.partition_key == "2026-11-20"
    assert request.run_key == "2026-11-20"


def test_materialize_one_partition_with_resource_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import dagster as dg

    settings = _make_settings(tmp_path)
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)
    defs = build_definitions()
    job = defs.resolve_job_def("daily_quotes_digest_job")

    result = job.execute_in_process(partition_key="2026-11-19")

    assert result.success
    assert (tmp_path / "export" / "quotes-2026-11-19.jsonl").is_file()
    assert (tmp_path / "digests" / "2026-11-19.md").is_file()
    assert (tmp_path / "audit" / "audit-2026-11-19.json").is_file()

    mat_events = result.get_asset_materialization_events()
    assert len(mat_events) == 1
    meta = mat_events[0].materialization.metadata
    assert isinstance(meta["coverage_table"], dg.TableMetadataValue)
    assert isinstance(meta["digest_preview"], dg.MarkdownMetadataValue)
    assert isinstance(meta["audit_report_preview"], dg.MarkdownMetadataValue)
    assert isinstance(meta["digest_path"], dg.PathMetadataValue)

    check_evals = result.get_asset_check_evaluations()
    check_results = {ev.check_name: ev.passed for ev in check_evals}
    assert check_results == {
        "export_schema_valid": True,
        "audit_gates_pass": True,
        "coverage_gaps_disclosed": True,
        "t0_rights_boundary_enforced": True,
        "quote_rights_limits_respected": True,
    }


def test_checks_read_their_own_topic_when_both_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from media_quotes.pipeline import run_daily_pipeline

    settings = _make_settings(tmp_path)
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)

    # A default-topic run leaves an audit file and export that must not be read by the
    # ai-policy asset checks below.
    run_daily_pipeline(settings, report_date=date(2026, 11, 19), topic_id="ukraine-war")

    defs = build_definitions()
    job = defs.resolve_job_def("daily_quotes_digest_job")
    result = job.execute_in_process(
        partition_key="2026-11-19",
        run_config={
            "ops": {
                "daily_quotes_digest": {
                    "config": {"topic_id": "ai-policy", "persist_to_postgres": False}
                }
            }
        },
    )
    assert result.success
    check_results = {ev.check_name: ev.passed for ev in result.get_asset_check_evaluations()}
    # ai-policy has no approved route for these fixtures, so its artifacts are empty.
    # The checks must still read those files, not the populated default-topic files.
    assert check_results == {
        "export_schema_valid": False,
        "audit_gates_pass": False,
        "coverage_gaps_disclosed": True,
        "t0_rights_boundary_enforced": False,
        "quote_rights_limits_respected": False,
    }
    assert (tmp_path / "audit" / "ai-policy" / "audit-2026-11-19.json").is_file()
    assert (tmp_path / "export" / "ai-policy" / "quotes-2026-11-19.jsonl").is_file()


def test_materialize_governance_job_in_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import dagster as dg

    settings = _make_settings(tmp_path)
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)
    defs = build_definitions()
    job = defs.resolve_job_def("governance_audit_job")

    result = job.execute_in_process()
    assert result.success
    assert (tmp_path / "audit" / "eval-synthetic-v1.json").is_file()

    eval_out = result.output_for_node("gold_set_evaluation")
    assert eval_out["pass"] is True
    assert eval_out["false_quote_rate"] == 0.0

    ret_out = result.output_for_node("retention_readiness_report")
    assert ret_out["audit_rows_dropped"] is False

    check_evals = result.get_asset_check_evaluations()
    assert any(ev.check_name == "gold_set_thresholds_met" and ev.passed for ev in check_evals)
    mat_events = {
        ev.materialization.asset_key.to_user_string(): ev.materialization.metadata
        for ev in result.get_asset_materialization_events()
    }
    assert isinstance(
        mat_events["gold_set_evaluation"]["summary_preview"], dg.MarkdownMetadataValue
    )
    assert isinstance(mat_events["retention_readiness_report"]["plan_json"], dg.JsonMetadataValue)


def test_review_queue_sla_sensor_healthy_and_overdue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import dagster as dg

    settings = _make_settings(tmp_path)
    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)
    defs = build_definitions()
    sensor = defs.sensors[0]

    # 1. Healthy empty queue
    ctx_healthy = dg.build_sensor_context(repository_def=defs.get_repository_def())
    res_healthy = sensor.evaluate_tick(ctx_healthy)
    assert res_healthy.skip_message is not None
    assert "0 overdue" in res_healthy.skip_message
    assert ctx_healthy.cursor is not None
    assert json.loads(ctx_healthy.cursor)["overdue_count"] == 0

    # 2. Overdue item (>24h old)
    overdue_time = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    queue_file = settings.audit_dir / "review-queue.json"
    queue_file.write_text(
        json.dumps(
            {
                "flags": [
                    {
                        "evidence": {
                            "old_quote_id": "Q-20261115-0001",
                            "old_content_hash": "a" * 64,
                            "newer_article_id": "art-2",
                            "newer_content_hash": "b" * 64,
                            "newer_is_first_hand": True,
                            "newer_rights_cleared": True,
                            "superseding_span": "Updated ceasefire statement.",
                            "decision_id": "dec-1",
                            "rationale": "Superseded by newer official statement.",
                        },
                        "confidence": 0.92,
                        "state": "pending",
                        "raised_at": overdue_time,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    ctx_overdue = dg.build_sensor_context(repository_def=defs.get_repository_def())
    res_overdue = sensor.evaluate_tick(ctx_overdue)
    assert res_overdue.skip_message is not None
    assert "ALERT: 1 overdue review item(s): Q-20261115-0001" in res_overdue.skip_message
    assert ctx_overdue.cursor is not None
    assert json.loads(ctx_overdue.cursor)["overdue_quote_ids"] == ["Q-20261115-0001"]
