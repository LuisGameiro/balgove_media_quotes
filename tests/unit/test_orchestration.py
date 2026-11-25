"""Dagster definitions and in-process materialization contract."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

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


CHECK_OPS = (
    "daily_quotes_digest_export_schema_valid",
    "daily_quotes_digest_audit_gates_pass",
    "daily_quotes_digest_coverage_gaps_disclosed",
    "daily_quotes_digest_t0_rights_boundary_enforced",
    "daily_quotes_digest_quote_rights_limits_respected",
    "neo4j_quote_projection_neo4j_projection_no_errors",
    "neo4j_quote_projection_neo4j_export_quotes_present",
    "neo4j_quote_projection_neo4j_projection_metadata_current",
)


def _fixture_check_config(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pin asset checks to fixture output, which is no longer auto-detected."""
    ops: dict[str, Any] = {name: {"config": {"source_mode": "fixtures"}} for name in CHECK_OPS}
    ops["daily_quotes_digest"] = {"config": {"source_mode": "fixtures"}}
    if extra:
        ops.update(extra)
    return {"ops": ops}


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


class _MockGraphResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def data(self) -> list[dict[str, Any]]:
        return self._rows


class _MockGraphSession:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.fail_writes = False
        self.projected: dict[str, dict[str, str]] = {}

    def run(
        self, query: str, parameters: dict[str, Any] | None = None, **kwargs: Any
    ) -> _MockGraphResult:
        if self.fail_writes and query.lstrip().startswith("MERGE"):
            raise RuntimeError("Neo4j unavailable")
        if "MERGE (q:Quote" in query and parameters:
            self.projected[str(parameters["quote_id"])] = {
                "topic_id": str(parameters.get("topic_id") or ""),
                "content_hash": str(
                    parameters.get("quote_content_hash") or parameters.get("content_hash") or ""
                ),
                "projected_at": str(parameters.get("projected_at") or ""),
            }
        if "ProjectionMetadata" in query:
            if parameters and "last_synced_at" in parameters:
                self.metadata = dict(parameters)
            return _MockGraphResult([self.metadata] if self.metadata else [])
        if "canonical_quote_id IN $quote_ids" in query:
            params = parameters or {}
            rows = [
                {
                    "quote_id": quote_id,
                    "content_hash": self.projected[quote_id]["content_hash"],
                    "projected_at": self.projected[quote_id]["projected_at"],
                }
                for quote_id in params.get("quote_ids", [])
                if quote_id in self.projected
                and self.projected[quote_id]["topic_id"] == params.get("topic_id")
            ]
            return _MockGraphResult(rows)
        return _MockGraphResult([])

    def close(self) -> None:
        pass


class _MockGraphDriver:
    def __init__(self, session: _MockGraphSession) -> None:
        self._session = session
        self.close_calls = 0

    def session(self, **kwargs: Any) -> _MockGraphSession:
        return self._session

    def verify_connectivity(self) -> None:
        pass

    def close(self) -> None:
        self.close_calls += 1


def _sample_export_record() -> Any:
    from media_quotes.models import ExportQuoteRecord, VerificationRecord

    return ExportQuoteRecord(
        quote_id="Q-20261119-0001",
        quote_text_original="Air defence deliveries will accelerate this winter.",
        quote_text_en="Air defence deliveries will accelerate this winter.",
        source_id="guardian",
        source_name="The Guardian",
        source_tier=2,
        source_country_region="United Kingdom",
        source_type="independent",
        rights_status="license_held",
        access_mode="public_rss_permitted",
        discovery_method="rss",
        claim_only=False,
        original_reporting_flag="first-hand",
        source_language="en",
        publication_date=date(2026, 11, 19),
        article_name="Ukraine air defence update",
        article_author="Reporter",
        article_url="https://www.theguardian.com/world/2026/nov/19/ukraine-air-defence",
        extraction_date=datetime(2026, 11, 19, 10, 0, tzinfo=UTC),
        section_id="war_intensity",
        is_obsolete=False,
        content_hash="a" * 64,
        verification=VerificationRecord(
            verbatim_match=True,
            metadata_verified=True,
            method="sha256-exact",
            verified_at=datetime(2026, 11, 19, 10, 5, tzinfo=UTC),
        ),
    )


def _fake_pipeline_result(settings: Settings, report_date: date, *, quote_count: int = 1) -> Any:
    from media_quotes.export import local_store_path, write_jsonl
    from media_quotes.pipeline import DailyPipelineResult

    records = tuple(_sample_export_record() for _ in range(quote_count))
    store_path = local_store_path(settings.export_dir, report_date)
    write_jsonl(records, store_path)
    checks = {
        "gates_pass": True,
        "coverage_gaps_disclosed": True,
        "export_schema_valid": True,
        "t0_rights_boundary_enforced": True,
        "quote_rights_limits_respected": True,
    }
    audit_path = settings.audit_dir / f"audit-{report_date.isoformat()}.json"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.write_text(
        json.dumps({"checks": checks, "coverage": [], "coverage_gaps": []}), encoding="utf-8"
    )
    digest_path = settings.digest_dir / f"{report_date.isoformat()}.md"
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text("# Test digest", encoding="utf-8")
    logs_path = settings.logs_dir / "test-run.jsonl"
    logs_path.parent.mkdir(parents=True, exist_ok=True)
    logs_path.write_text("", encoding="utf-8")
    return DailyPipelineResult(
        run_id="test-run",
        report_date=report_date,
        dry_run=False,
        records=records,
        coverage=(),
        gaps=(),
        events=(),
        rejected=0,
        rejected_total=0,
        status="success",
        topic_id="ukraine-war",
        store_path=store_path,
        digest_path=digest_path,
        logs_path=logs_path,
        audit_json_path=audit_path,
        checks=checks,
    )


def test_definitions_expose_asset_job_checks_and_schedule() -> None:
    defs = build_definitions()
    assert sorted(asset.key.to_user_string() for asset in defs.assets) == [
        "daily_quotes_digest",
        "gold_set_evaluation",
        "neo4j_quote_projection",
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
        "neo4j_export_quotes_present",
        "neo4j_projection_metadata_current",
        "neo4j_projection_no_errors",
        "quote_rights_limits_respected",
        "t0_rights_boundary_enforced",
    ]
    assert PARTITION_START == "2026-10-01"
    assert CODE_VERSION == "c4-daily-pipeline-v3"


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
    daily_config = request.run_config["ops"]["daily_quotes_digest"]["config"]
    assert daily_config["source_mode"] == "live"


def test_materialize_one_partition_with_resource_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import dagster as dg

    settings = _make_settings(tmp_path)
    test_settings = settings.for_source_mode("fixtures")
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)
    defs = build_definitions()
    job = defs.resolve_job_def("daily_quotes_digest_job")

    result = job.execute_in_process(partition_key="2026-11-19", run_config=_fixture_check_config())

    assert result.success
    assert (test_settings.export_dir / "quotes-2026-11-19.jsonl").is_file()
    assert (test_settings.digest_dir / "2026-11-19.md").is_file()
    assert (test_settings.audit_dir / "audit-2026-11-19.json").is_file()

    mat_events = result.get_asset_materialization_events()
    assert len(mat_events) == 2
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
        "neo4j_projection_no_errors": True,
        "neo4j_export_quotes_present": True,
        "neo4j_projection_metadata_current": True,
    }


def test_graph_projection_rejects_missing_source_mode(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path).model_copy(update={"sync_to_neo4j": True})

    assert (
        orchestration._graph_skip_reason(settings, {"quotes": 1, "source_mode": ""})
        == "source mode missing or unsafe"
    )
    assert orchestration._graph_skip_reason(settings, {"quotes": 1, "source_mode": "blobs"}) == ""


def test_graph_asset_skips_fixture_replays(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _make_settings(tmp_path).model_copy(update={"sync_to_neo4j": True})
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)

    def forbidden_driver(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("fixture replay must not open a Neo4j driver")

    monkeypatch.setattr(orchestration.Neo4jResourceHandle, "get_driver", forbidden_driver)
    monkeypatch.setattr("media_quotes.graph.get_driver", forbidden_driver)
    defs = build_definitions()

    result = defs.resolve_job_def("daily_quotes_digest_job").execute_in_process(
        partition_key="2026-11-19",
        run_config=_fixture_check_config(),
    )

    assert result.success
    graph_output = result.output_for_node("neo4j_quote_projection")
    assert graph_output["status"] == "skipped"
    assert graph_output["skip_reason"] == "fixture replay cannot write to the live graph"


def test_graph_checks_pass_for_empty_live_partition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _make_settings(tmp_path).model_copy(update={"sync_to_neo4j": True})
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)

    def forbidden_driver(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("an empty partition must not open Neo4j")

    monkeypatch.setattr("media_quotes.graph.client.get_driver", forbidden_driver)
    monkeypatch.setattr(
        orchestration,
        "run_daily_pipeline",
        lambda app_settings, **kwargs: _fake_pipeline_result(
            app_settings, kwargs["report_date"], quote_count=0
        ),
    )
    defs = build_definitions()

    result = defs.resolve_job_def("daily_quotes_digest_job").execute_in_process(
        partition_key="2026-11-19",
        run_config={"ops": {"daily_quotes_digest": {"config": {"source_mode": "blobs"}}}},
    )

    assert result.success
    assert result.output_for_node("neo4j_quote_projection")["status"] == "skipped"
    graph_checks = {
        ev.check_name: ev.passed
        for ev in result.get_asset_check_evaluations()
        if ev.check_name.startswith("neo4j_")
    }
    assert graph_checks == {
        "neo4j_projection_no_errors": True,
        "neo4j_export_quotes_present": True,
        "neo4j_projection_metadata_current": True,
    }


def test_graph_checks_fail_for_missing_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing operational export must fail checks even when the asset ran on fixtures."""
    settings = _make_settings(tmp_path).model_copy(update={"sync_to_neo4j": True})
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)
    defs = build_definitions()
    ops: dict[str, Any] = {"daily_quotes_digest": {"config": {"source_mode": "fixtures"}}}
    for name in (
        "neo4j_quote_projection_neo4j_projection_no_errors",
        "neo4j_quote_projection_neo4j_export_quotes_present",
        "neo4j_quote_projection_neo4j_projection_metadata_current",
    ):
        ops[name] = {"config": {"source_mode": "blobs"}}
    result = defs.resolve_job_def("daily_quotes_digest_job").execute_in_process(
        partition_key="2026-11-19", run_config={"ops": ops}
    )

    assert result.success
    checks = {
        ev.check_name: ev.passed
        for ev in result.get_asset_check_evaluations()
        if ev.check_name.startswith("neo4j_")
    }
    assert checks == {
        "neo4j_projection_no_errors": False,
        "neo4j_export_quotes_present": False,
        "neo4j_projection_metadata_current": False,
    }


@pytest.mark.parametrize(
    ("mutate", "expect_current"),
    [
        (lambda rec: rec.model_copy(update={"topic_id": "ai-policy"}), False),
        (lambda rec: rec.model_copy(update={"content_hash": "b" * 64}), True),
    ],
)
def test_graph_checks_are_partition_scoped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutate: Any, expect_current: bool
) -> None:
    from media_quotes.graph import project_records_to_graph as real_project

    settings = _make_settings(tmp_path).model_copy(update={"sync_to_neo4j": True})
    session = _MockGraphSession()
    driver = _MockGraphDriver(session)
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)
    monkeypatch.setattr("media_quotes.graph.client.get_driver", lambda _settings: driver)
    monkeypatch.setattr(
        "media_quotes.graph.project_records_to_graph",
        lambda records, graph_session: real_project(
            [mutate(rec) for rec in records], graph_session
        ),
    )
    monkeypatch.setattr(
        orchestration,
        "run_daily_pipeline",
        lambda app_settings, **kwargs: _fake_pipeline_result(
            app_settings, date.fromisoformat(kwargs["report_date"].isoformat())
        ),
    )
    defs = build_definitions()
    result = defs.resolve_job_def("daily_quotes_digest_job").execute_in_process(
        partition_key="2026-11-19",
        run_config={"ops": {"daily_quotes_digest": {"config": {"source_mode": "blobs"}}}},
    )

    assert result.success
    checks = {
        ev.check_name: ev.passed
        for ev in result.get_asset_check_evaluations()
        if ev.check_name.startswith("neo4j_")
    }
    assert checks["neo4j_export_quotes_present"] is False
    assert checks["neo4j_projection_metadata_current"] is expect_current


def test_graph_checks_use_each_quotes_own_partition_start(tmp_path: Path) -> None:
    from datetime import time

    from media_quotes.export import local_store_path, write_jsonl

    settings = _make_settings(tmp_path)
    topic = "ukraine-war"
    dates = [date(2026, 11, 18), date(2026, 11, 19)]
    older = _sample_export_record().model_copy(
        update={
            "quote_id": "Q-20261118-0001",
            "publication_date": date(2026, 11, 18),
            "content_hash": "a" * 64,
        }
    )
    newer = _sample_export_record().model_copy(
        update={
            "quote_id": "Q-20261119-0001",
            "publication_date": date(2026, 11, 19),
            "content_hash": "b" * 64,
        }
    )
    for report_date, record in zip(dates, (older, newer), strict=True):
        write_jsonl([record], local_store_path(settings.export_dir, report_date, topic_id=topic))

    expected, missing = orchestration._graph_export_quotes(settings, dates, topic)
    assert missing == []
    assert (
        expected[older.quote_id][1]
        == datetime.combine(date(2026, 11, 18), time.min, tzinfo=UTC).isoformat()
    )

    projected = {
        older.quote_id: ("a" * 64, "2026-11-18T12:00:00+00:00"),  # own partition day: fresh
        newer.quote_id: ("b" * 64, "2026-11-18T23:00:00+00:00"),  # before own day: stale
    }
    assert orchestration._graph_stale_quotes(expected, projected, check_content=True) == [
        newer.quote_id
    ]
    projected[newer.quote_id] = ("c" * 64, "2026-11-19T12:00:00+00:00")
    assert orchestration._graph_stale_quotes(expected, projected, check_content=True) == [
        newer.quote_id
    ]


def test_graph_asset_projects_live_partition_and_runs_checks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _make_settings(tmp_path).model_copy(update={"sync_to_neo4j": True})
    session = _MockGraphSession()
    driver = _MockGraphDriver(session)
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)
    monkeypatch.setattr("media_quotes.graph.client.get_driver", lambda _settings: driver)
    monkeypatch.setattr(
        orchestration,
        "run_daily_pipeline",
        lambda app_settings, **kwargs: _fake_pipeline_result(
            app_settings, date.fromisoformat(kwargs["report_date"].isoformat())
        ),
    )
    defs = build_definitions()

    result = defs.resolve_job_def("daily_quotes_digest_job").execute_in_process(
        partition_key="2026-11-19",
        run_config={"ops": {"daily_quotes_digest": {"config": {"source_mode": "blobs"}}}},
    )

    assert result.success
    graph_output = result.output_for_node("neo4j_quote_projection")
    assert graph_output["status"] == "ok"
    assert graph_output["quotes_projected"] == 1
    assert graph_output["errors"] == []
    assert driver.close_calls == 1
    graph_checks = {
        ev.check_name: ev.passed
        for ev in result.get_asset_check_evaluations()
        if ev.check_name.startswith("neo4j_")
    }
    assert graph_checks == {
        "neo4j_projection_no_errors": True,
        "neo4j_export_quotes_present": True,
        "neo4j_projection_metadata_current": True,
    }


def test_graph_failure_is_isolated_from_digest_asset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _make_settings(tmp_path).model_copy(update={"sync_to_neo4j": True})
    session = _MockGraphSession()
    session.fail_writes = True
    driver = _MockGraphDriver(session)
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)
    monkeypatch.setattr("media_quotes.graph.client.get_driver", lambda _settings: driver)
    monkeypatch.setattr(
        orchestration,
        "run_daily_pipeline",
        lambda app_settings, **kwargs: _fake_pipeline_result(
            app_settings, date.fromisoformat(kwargs["report_date"].isoformat())
        ),
    )
    defs = build_definitions()

    result = defs.resolve_job_def("daily_quotes_digest_job").execute_in_process(
        partition_key="2026-11-19",
        run_config={"ops": {"daily_quotes_digest": {"config": {"source_mode": "blobs"}}}},
        raise_on_error=False,
    )

    assert not result.success
    assert result.output_for_node("daily_quotes_digest")["status"] == "success"
    assert driver.close_calls == 1


def test_checks_read_their_own_topic_when_both_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from media_quotes.pipeline import run_daily_pipeline

    settings = _make_settings(tmp_path)
    test_settings = settings.for_source_mode("fixtures")
    monkeypatch.setattr(orchestration, "get_settings", lambda: settings)

    # A default-topic run leaves an audit file and export that must not be read by the
    # ai-policy asset checks below.
    run_daily_pipeline(
        settings,
        report_date=date(2026, 11, 19),
        topic_id="ukraine-war",
        source_mode="fixtures",
    )

    defs = build_definitions()
    job = defs.resolve_job_def("daily_quotes_digest_job")
    result = job.execute_in_process(
        partition_key="2026-11-19",
        run_config=_fixture_check_config(
            extra={
                "daily_quotes_digest": {
                    "config": {
                        "source_mode": "fixtures",
                        "topic_id": "ai-policy",
                        "persist_to_postgres": False,
                    }
                }
            }
        ),
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
        "neo4j_projection_no_errors": True,
        "neo4j_export_quotes_present": True,
        "neo4j_projection_metadata_current": True,
    }
    assert (test_settings.audit_dir / "ai-policy" / "audit-2026-11-19.json").is_file()
    assert (test_settings.export_dir / "ai-policy" / "quotes-2026-11-19.jsonl").is_file()


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


def test_partitioned_assets_share_single_run_backfill_policy() -> None:
    defs = build_definitions()
    policies = {
        asset.key.to_user_string(): asset.backfill_policy
        for asset in defs.assets
        if asset.backfill_policy is not None
    }

    assert policies["daily_quotes_digest"].single_run
    assert policies["neo4j_quote_projection"].single_run


def test_range_helper_uses_shared_service_and_emits_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dagster as dg

    from media_quotes.backfill import BackfillRequest, BackfillResult, plan_backfill

    settings = _make_settings(tmp_path)
    config = orchestration.DailyPipelineConfig(
        source_mode="blobs",
        max_cost=100.0,
        fetch_archive=True,
        max_archive_articles=5,
        persist_to_postgres=False,
    )
    request = BackfillRequest(
        start=date(2026, 11, 18),
        end=date(2026, 11, 19),
        max_cost=100.0,
        execute=True,
        source_mode="blobs",
        fetch_archive=True,
        max_archive_articles=5,
        pipeline="dagster-backfill",
    )
    plan = plan_backfill(settings, request)
    calls: list[dict[str, Any]] = []

    def fake_execute(*args: Any, **kwargs: Any) -> BackfillResult:
        calls.append(kwargs)
        return BackfillResult(plan=plan, total_quotes=3, total_gaps=1)

    monkeypatch.setattr(orchestration, "execute_backfill", fake_execute)
    context = SimpleNamespace(log=MagicMock(), add_output_metadata=MagicMock())
    database = MagicMock()

    summary = orchestration._run_backfill_range(
        context,
        config,
        settings,
        database,
        MagicMock(),
        dg.PartitionKeyRange(start="2026-11-18", end="2026-11-19"),
    )

    assert calls[0]["plan"] == plan
    assert calls[0]["session_context"] is database.context.return_value
    assert summary["span_days"] == 2
    assert summary["quotes"] == 3
    assert summary["gaps"] == 1
    context.add_output_metadata.assert_called_once()


def test_sync_backfill_graph_skips_without_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no database engine without PERSIST_TO_POSTGRES")

    monkeypatch.setattr("media_quotes.db.engine.create_db_engine", forbidden)
    settings = _make_settings(tmp_path).model_copy(update={"sync_to_neo4j": True})
    metadata = orchestration._sync_backfill_graph(
        SimpleNamespace(log=MagicMock()), settings, "ukraine-war", MagicMock()
    )
    assert metadata["neo4j_sync_status"] == "skipped"
    assert "persist_to_postgres" in metadata["neo4j_skip_reason"]


def test_sync_backfill_graph_isolates_engine_setup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken_engine(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("engine unavailable")

    monkeypatch.setattr("media_quotes.db.engine.create_db_engine", broken_engine)
    settings = _make_settings(tmp_path).model_copy(
        update={"sync_to_neo4j": True, "persist_to_postgres": True}
    )
    context = SimpleNamespace(log=MagicMock())
    metadata = orchestration._sync_backfill_graph(context, settings, "ukraine-war", MagicMock())
    assert metadata["neo4j_sync_status"] == "error"
    assert "engine unavailable" in metadata["neo4j_sync_error"]
    context.log.warning.assert_called_once()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"dry_run": True}, "dry_run"),
        ({"source_mode": "fixtures"}, "source_mode"),
        ({"max_cost": None}, "max_cost"),
        ({"tier": 1}, "tier filter"),
    ],
)
def test_range_helper_rejects_unsafe_config(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    import dagster as dg

    from media_quotes.exceptions import ConfigError

    values: dict[str, object] = {
        "source_mode": "blobs",
        "max_cost": 100.0,
        "fetch_archive": True,
    }
    values.update(overrides)
    config = orchestration.DailyPipelineConfig(**values)  # type: ignore[arg-type]

    with pytest.raises(ConfigError, match=message):
        orchestration._run_backfill_range(
            SimpleNamespace(log=MagicMock(), add_output_metadata=MagicMock()),
            config,
            _make_settings(tmp_path),
            MagicMock(),
            MagicMock(),
            dg.PartitionKeyRange(start="2026-11-18", end="2026-11-19"),
        )


def test_range_helper_rejects_ranges_over_365_days(tmp_path: Path) -> None:
    import dagster as dg

    from media_quotes.exceptions import ConfigError

    config = orchestration.DailyPipelineConfig(source_mode="blobs", max_cost=100.0)
    with pytest.raises(ConfigError, match="365"):
        orchestration._run_backfill_range(
            SimpleNamespace(log=MagicMock(), add_output_metadata=MagicMock()),
            config,
            _make_settings(tmp_path),
            MagicMock(),
            MagicMock(),
            dg.PartitionKeyRange(start="2026-01-01", end="2027-01-01"),
        )


def test_range_helpers_expand_dates_and_detect_output_root(tmp_path: Path) -> None:
    import dagster as dg

    settings = _make_settings(tmp_path)
    context = SimpleNamespace(
        partition_key_range=dg.PartitionKeyRange(start="2026-11-18", end="2026-11-19")
    )
    dates = orchestration._check_dates(context, settings)
    assert dates == [date(2026, 11, 18), date(2026, 11, 19)]

    test_settings = settings.for_source_mode("fixtures")
    test_settings.audit_dir.mkdir(parents=True)
    (test_settings.audit_dir / "audit-2026-11-18.json").write_text("{}", encoding="utf-8")
    assert orchestration._check_settings(settings, orchestration.CheckConfig(), dates) == settings
    assert (
        orchestration._check_settings(
            settings, orchestration.CheckConfig(source_mode="fixtures"), dates
        )
        == test_settings
    )

    (settings.audit_dir / "audit-2026-11-18.json").parent.mkdir(parents=True, exist_ok=True)
    (settings.audit_dir / "audit-2026-11-18.json").write_text("{}", encoding="utf-8")
    assert orchestration._check_settings(settings, orchestration.CheckConfig(), dates) == settings

    test_topic_dir = test_settings.audit_dir / "ai-policy"
    test_topic_dir.mkdir(parents=True)
    (test_topic_dir / "audit-2026-11-18.json").write_text("{}", encoding="utf-8")
    topic_config = orchestration.CheckConfig(source_mode="fixtures", topic_id="ai-policy")
    assert orchestration._check_settings(settings, topic_config, dates) == test_settings


def test_check_settings_defaults_to_operational_output(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    test_settings = settings.for_source_mode("fixtures")
    dates = [date(2026, 11, 19)]
    name = "audit-2026-11-19.json"
    (settings.audit_dir / name).parent.mkdir(parents=True, exist_ok=True)
    (settings.audit_dir / name).write_text("{}", encoding="utf-8")
    fixture_report = test_settings.audit_dir / name
    fixture_report.parent.mkdir(parents=True, exist_ok=True)
    fixture_report.write_text("{}", encoding="utf-8")

    assert orchestration._check_settings(settings, orchestration.CheckConfig(), dates) == settings
    assert (
        orchestration._check_settings(
            settings, orchestration.CheckConfig(source_mode="fixtures"), dates
        )
        == test_settings
    )
