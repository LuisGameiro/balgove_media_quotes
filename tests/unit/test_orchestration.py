"""Dagster definitions and in-process materialization contract."""

from __future__ import annotations

import json
from contextlib import contextmanager
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
        "live_export_has_no_fixture_records",
        "quote_rights_limits_respected",
        "retention_index_bloat_within_limit",
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


def test_schedules_request_live_collection_but_stay_stopped() -> None:
    import dagster as dg

    defs = build_definitions()
    for schedule, expected_tier in zip(defs.schedules, (None, 1), strict=True):
        # Schedules ship STOPPED; enabling one later performs live collection.
        assert schedule.default_status == dg.DefaultScheduleStatus.STOPPED
        context = dg.build_schedule_context(
            repository_def=defs.get_repository_def(),
            scheduled_execution_time=datetime(2026, 11, 20, 6, tzinfo=UTC),
        )
        request = schedule._execution_fn.decorated_fn(context)
        assert request is not None
        run_config = request.run_config["ops"]["daily_quotes_digest"]["config"]
        assert run_config["source_mode"] == "live"
        assert run_config.get("tier") == expected_tier


def test_daily_pipeline_config_rejects_unknown_source_mode() -> None:
    from pydantic import ValidationError

    from media_quotes.orchestration import DailyPipelineConfig

    assert DailyPipelineConfig(source_mode="live").source_mode == "live"
    with pytest.raises(ValidationError, match="unsupported source_mode"):
        DailyPipelineConfig(source_mode="direct-network")


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
    assert (tmp_path / "export" / "fixtures" / "quotes-2026-11-19.jsonl").is_file()
    assert (tmp_path / "digests" / "fixtures" / "2026-11-19.md").is_file()
    assert (tmp_path / "audit" / "fixtures" / "audit-2026-11-19.json").is_file()

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
        "live_export_has_no_fixture_records": True,
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
        "live_export_has_no_fixture_records": True,
    }
    assert (tmp_path / "audit" / "fixtures" / "ai-policy" / "audit-2026-11-19.json").is_file()
    assert (tmp_path / "export" / "fixtures" / "ai-policy" / "quotes-2026-11-19.jsonl").is_file()


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
    assert ret_out["partitions_to_detach"] == []
    assert ret_out["partitions_hot"] == []
    assert "longest_txn_ok" not in ret_out  # lock duration is never measured here

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


def test_observed_retention_months_helper_gates_on_persistence(tmp_path: Path) -> None:
    from media_quotes.db.resources import DatabaseResourceHandle

    class _MonthRows:
        def __init__(self, months: list[datetime]) -> None:
            self._months = months

        def scalars(self) -> _MonthRows:
            return self

        def all(self) -> list[datetime]:
            return self._months

    class _ObservedSession:
        def execute(self, statement: object) -> _MonthRows:
            return _MonthRows([datetime(2026, 10, 6, 9, 0), datetime(2026, 11, 3, 9, 0)])

    @contextmanager
    def session_factory(_settings: Settings):  # type: ignore[no-untyped-def]
        yield _ObservedSession()

    database = DatabaseResourceHandle(session_factory)
    offline = _make_settings(tmp_path)  # persist_to_postgres=False
    assert orchestration._observed_retention_months(offline, database) == []
    live = offline.model_copy(update={"persist_to_postgres": True})
    assert orchestration._observed_retention_months(live, database) == [
        date(2026, 10, 6),
        date(2026, 11, 3),
    ]


def test_retention_uses_observed_months_when_persisting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import media_quotes.restore_drill as restore_drill

    settings = _make_settings(tmp_path).model_copy(update={"persist_to_postgres": True})
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)

    observed = [date(2026, 10, 1), date(2026, 11, 1)]
    monkeypatch.setattr(orchestration, "_observed_retention_months", lambda s, d: observed)

    readiness_calls: list[list[date]] = []
    real_readiness = restore_drill.retention_readiness

    def spy_readiness(policy, today, partitions, **kwargs):  # type: ignore[no-untyped-def]
        readiness_calls.append(list(partitions))
        return real_readiness(policy, today, partitions, **kwargs)

    monkeypatch.setattr(restore_drill, "retention_readiness", spy_readiness)

    defs = build_definitions()
    result = defs.resolve_job_def("governance_audit_job").execute_in_process()
    assert result.success

    ret_out = result.output_for_node("retention_readiness_report")
    assert ret_out["partitions_to_detach"] == ["audit_events_2026_10"]
    assert ret_out["partitions_hot"] == ["audit_events_2026_11"]
    assert ret_out["partition_months_observed"] is True
    # The asset and its index-bloat check both plan from the observed months.
    assert len(readiness_calls) >= 2
    assert all(months == observed for months in readiness_calls)


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


def _queue_flag(quote_id: str, *, overdue: bool) -> dict[str, Any]:
    raised_at = (
        datetime.now(UTC) - timedelta(days=3) if overdue else datetime.now(UTC)
    ).isoformat()
    return {
        "evidence": {
            "old_quote_id": quote_id,
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
        "raised_at": raised_at,
    }


def test_review_queue_sensor_handles_multiple_provenances(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import dagster as dg

    settings = _make_settings(tmp_path)
    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)
    # The same overdue quote ID appears in two queues; each queue must be built separately.
    queues = {
        "review-queue.json": [
            _queue_flag("Q-SHARED", overdue=True),
            _queue_flag("Q-FIX", overdue=False),
        ],
        "review-queue-live_feed.json": [
            _queue_flag("Q-SHARED", overdue=True),
            _queue_flag("Q-LIVE", overdue=False),
        ],
        "review-queue-manual_sample.json": [_queue_flag("Q-MANUAL", overdue=False)],
        "review-queue-live_bing.json": [],
    }
    for name, flags in queues.items():
        (settings.audit_dir / name).write_text(json.dumps({"flags": flags}), encoding="utf-8")

    defs = build_definitions()
    sensor = defs.sensors[0]
    context = dg.build_sensor_context(repository_def=defs.get_repository_def())
    result = sensor.evaluate_tick(context)

    assert result.skip_message is not None
    assert "ALERT: 1 overdue review item(s): Q-SHARED" in result.skip_message
    assert "fixture:" in result.skip_message
    assert "live_feed:" in result.skip_message
    assert context.cursor is not None
    cursor = json.loads(context.cursor)
    assert cursor["queues"]["fixture"]["pending_count"] == 2
    assert cursor["queues"]["live_feed"]["pending_count"] == 2
    assert cursor["queues"]["manual_sample"]["pending_count"] == 1
    assert cursor["queues"]["live_bing"]["pending_count"] == 0
    assert cursor["queues"]["fixture"]["overdue_count"] == 1
    assert cursor["queues"]["live_feed"]["overdue_count"] == 1
    assert cursor["overdue_quote_ids"] == ["Q-SHARED"]

    # A second tick with the same state does not re-alert.
    context2 = dg.build_sensor_context(
        repository_def=defs.get_repository_def(), cursor=context.cursor
    )
    result2 = sensor.evaluate_tick(context2)
    assert result2.skip_message == "Review SLA alert already sent."


def _write_audit(
    tmp_path: Path,
    day: str,
    payload: dict[str, Any],
    *,
    fixture: bool = False,
    topic: str | None = None,
) -> Path:
    root = tmp_path / "audit" / "fixtures" if fixture else tmp_path / "audit"
    if topic and topic != "ukraine-war":
        root = root / topic
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"audit-{day}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _audit_payload(
    day: str,
    *,
    provenance: str = "fixture",
    source_mode: str = "fixtures",
    topic_id: str = "ukraine-war",
) -> dict[str, Any]:
    return {
        "topic_id": topic_id,
        "report_date": day,
        "source_mode": source_mode,
        "provenance": provenance,
        "coverage": [],
        "coverage_gaps": [],
        "checks": {"gates_pass": True},
    }


class _PartitionedCheckContext:
    """Minimal context: checks only read the partition key from it."""

    asset_partition_key = "2026-11-19"


def _check_result(defs: Any, name: str, settings: Settings) -> Any:
    from media_quotes.orchestration import CheckConfig

    group = next(
        group for group in defs.asset_checks if any(key.name == name for key in group.check_keys)
    )
    check_fn = group.computation.node_def.compute_fn.decorated_fn
    return check_fn(_PartitionedCheckContext(), CheckConfig(), settings)


def test_provenance_helpers_prefer_live_audit(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    day = date(2026, 11, 19)
    _write_audit(tmp_path, "2026-11-19", _audit_payload("2026-11-19"), fixture=True)

    report, provenance = orchestration._selected_audit(settings, day, "ukraine-war")
    assert provenance == "fixture"
    assert report["source_mode"] == "fixtures"

    _write_audit(
        tmp_path,
        "2026-11-19",
        _audit_payload("2026-11-19", provenance="live_feed", source_mode="live"),
    )
    report, provenance = orchestration._selected_audit(settings, day, "ukraine-war")
    assert provenance == "live_feed"
    assert report["source_mode"] == "live"
    # An explicit provenance filter still reads the matching tree.
    assert (
        orchestration._load_audit_report(settings, day, "ukraine-war", "fixture")["source_mode"]
        == "fixtures"
    )


def test_check_topic_never_treats_fixtures_as_topic(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    day = date(2026, 11, 19)
    _write_audit(tmp_path, "2026-11-19", _audit_payload("2026-11-19"), fixture=True)
    assert orchestration._check_topic(settings, day, None) == "ukraine-war"

    # A live audit for a named topic wins over the fixture audit for the default topic.
    _write_audit(
        tmp_path,
        "2026-11-19",
        _audit_payload("2026-11-19", topic_id="ai-policy"),
        topic="ai-policy",
    )
    assert orchestration._check_topic(settings, day, None) == "ai-policy"


def test_check_topic_never_treats_runs_as_topic(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    day = date(2026, 11, 19)
    scoped = tmp_path / "audit" / "runs" / "archive" / "ai-policy"
    scoped.mkdir(parents=True)
    (scoped / f"audit-{day.isoformat()}.json").write_text(
        json.dumps(_audit_payload(day.isoformat(), topic_id="ai-policy")), encoding="utf-8"
    )
    assert orchestration._check_topic(settings, day, None) == "ukraine-war"


def test_schema_check_reads_selected_provenance(tmp_path: Path) -> None:
    from media_quotes.pipeline import run_daily_pipeline

    settings = _make_settings(tmp_path)
    day = date(2026, 11, 19)
    settings.report_date = day
    run_daily_pipeline(settings, report_date=day, topic_id="ukraine-war")

    fixture_export = tmp_path / "export" / "fixtures" / "quotes-2026-11-19.jsonl"
    live_export = tmp_path / "export" / "quotes-2026-11-19.jsonl"
    live_export.write_bytes(fixture_export.read_bytes())
    _write_audit(
        tmp_path,
        "2026-11-19",
        _audit_payload("2026-11-19", provenance="live_feed", source_mode="live"),
    )
    fixture_export.write_text("", encoding="utf-8")  # only the live export has records

    defs = build_definitions()
    assert _check_result(defs, "export_schema_valid", settings).passed is True

    # No live audit: fall back to the empty fixture export and fail.
    (tmp_path / "audit" / "audit-2026-11-19.json").unlink()
    assert _check_result(defs, "export_schema_valid", settings).passed is False


def test_live_export_check_fails_on_fixture_records(tmp_path: Path) -> None:
    from media_quotes.pipeline import run_daily_pipeline

    settings = _make_settings(tmp_path)
    day = date(2026, 11, 19)
    settings.report_date = day
    run_daily_pipeline(settings, report_date=day, topic_id="ukraine-war")

    fixture_export = tmp_path / "export" / "fixtures" / "quotes-2026-11-19.jsonl"
    live_export = tmp_path / "export" / "quotes-2026-11-19.jsonl"
    live_export.write_bytes(fixture_export.read_bytes())  # fixture-stamped records at live path
    _write_audit(
        tmp_path,
        "2026-11-19",
        _audit_payload("2026-11-19", provenance="live_feed", source_mode="live"),
    )

    defs = build_definitions()
    failed = _check_result(defs, "live_export_has_no_fixture_records", settings)
    assert failed.passed is False
    assert failed.metadata["fixture_records"].value >= 1

    clean = "\n".join(
        json.dumps({**json.loads(line), "provenance": "live_feed"})
        for line in fixture_export.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    live_export.write_text(clean + "\n", encoding="utf-8")
    assert _check_result(defs, "live_export_has_no_fixture_records", settings).passed is True


def test_live_export_check_skips_fixture_runs(tmp_path: Path) -> None:
    import dagster as dg

    settings = _make_settings(tmp_path)
    settings.report_date = date(2026, 11, 19)
    defs = build_definitions()
    result = _check_result(defs, "live_export_has_no_fixture_records", settings)
    assert result.passed is True
    assert result.severity == dg.AssetCheckSeverity.WARN
