"""Contracts for the shared CLI/Dagster historical backfill service."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_quotes import backfill
from media_quotes.archive import ArchiveResult
from media_quotes.config import Settings
from media_quotes.exceptions import BudgetExceededError, ConfigError

ROOT = Path(__file__).resolve().parents[2]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        output_dir=tmp_path / "output",
        persist_to_postgres=False,
        sources_csv=ROOT / "data/sources.csv",
        sources_dir=ROOT / "data/sources",
        sections_csv=ROOT / "data/sections.csv",
        schema_path=ROOT / "data/quotes.schema.json",
        fixtures_dir=ROOT / "data/fixtures",
        gold_set_dir=ROOT / "data/gold_set",
    )


def _request(**overrides: object) -> backfill.BackfillRequest:
    values: dict[str, object] = {
        "start": date(2026, 11, 18),
        "end": date(2026, 11, 19),
        "max_cost": 100.0,
        "execute": True,
    }
    values.update(overrides)
    return backfill.BackfillRequest(**values)  # type: ignore[arg-type]


def test_plan_validates_range_budget_and_archive_limit(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(ConfigError, match="precede"):
        backfill.plan_backfill(settings, _request(start=date(2026, 11, 20)))
    with pytest.raises(ConfigError, match="max-cost"):
        backfill.plan_backfill(settings, _request(max_cost=None))
    with pytest.raises(ConfigError, match="positive"):
        backfill.plan_backfill(settings, _request(fetch_archive=True, max_archive_articles=0))


def test_budget_gate_blocks_before_archive_or_partitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("budget rejection must happen before execution")

    monkeypatch.setattr(backfill, "_discover_archive", unexpected)
    monkeypatch.setattr("media_quotes.pipeline.run_daily_pipeline", unexpected)

    with pytest.raises(BudgetExceededError):
        backfill.execute_backfill(settings, _request(max_cost=0.01, fetch_archive=True))


def test_execute_archive_then_runs_daily_blob_partitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    calls: list[dict[str, Any]] = []
    archive = ArchiveResult(
        source_id="guardian",
        feed_url="https://www.theguardian.com/world/ukraine/rss",
        discovered_urls=2,
        stored_articles=2,
    )
    monkeypatch.setattr(backfill, "_discover_archive", lambda *args, **kwargs: [archive])

    def fake_pipeline(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return SimpleNamespace(
            report_date=kwargs["report_date"],
            run_id=f"run-{kwargs['report_date']}",
            records=[object()],
            gaps=[{}],
            rejected_total=2,
            status="partial",
            persistence={"stored_quotes": 1},
            store_path=tmp_path / "quotes.jsonl",
            digest_path=tmp_path / "digest.md",
            logs_path=tmp_path / "run.jsonl",
            audit_json_path=tmp_path / "audit.json",
        )

    monkeypatch.setattr("media_quotes.pipeline.run_daily_pipeline", fake_pipeline)

    result = backfill.execute_backfill(
        settings,
        _request(fetch_archive=True, max_archive_articles=2, pipeline="dagster-backfill"),
    )

    assert result.archives == [archive]
    assert [call["report_date"] for call in calls] == [date(2026, 11, 18), date(2026, 11, 19)]
    assert all(call["source_mode"] == "blobs" for call in calls)
    assert all(call["pipeline"] == "dagster-backfill" for call in calls)
    assert result.total_quotes == 2
    assert result.total_gaps == 2
    assert result.persisted_quotes == 2


def test_archive_skips_disabled_sources_and_passes_redirect_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    rows = [
        SimpleNamespace(
            source_id="ap",
            tier=2,
            usable=False,
            tier_block=False,
            discovery_method="rss",
            feed_url="https://apnews.com/index.rss",
        ),
        SimpleNamespace(
            source_id="ukrainskapravda",
            tier=3,
            usable=True,
            tier_block=False,
            discovery_method="rss",
            feed_url="https://www.pravda.com.ua/rss/",
        ),
    ]
    config = SimpleNamespace(
        discovery_feed_url=lambda _topic: "https://www.pravda.com.ua/rss/",
        redirect_hosts=["eurointegration.com.ua"],
    )
    calls: list[dict[str, Any]] = []

    def fake_discover(row: Any, feed_url: str, **kwargs: Any) -> ArchiveResult:
        calls.append({"source_id": row.source_id, "feed_url": feed_url, **kwargs})
        return ArchiveResult(source_id=row.source_id, feed_url=feed_url, stored_articles=1)

    monkeypatch.setattr(backfill, "_load_rows", lambda _settings: rows)
    monkeypatch.setattr(
        "media_quotes.cli.common._load_source_configs",
        lambda _settings: {"ukrainskapravda": config},
    )
    monkeypatch.setattr("media_quotes.cli.common._load_topic_pack", lambda *args: None)
    monkeypatch.setattr("media_quotes.archive.discover_archive_documents", fake_discover)

    backfill._discover_archive(
        settings,
        _request(fetch_archive=True, max_archive_articles=3),
        SimpleNamespace(effective_end=date(2026, 11, 19)),
    )

    assert [call["source_id"] for call in calls] == ["ukrainskapravda"]
    assert calls[0]["policy"].follow_redirects is True
    assert calls[0]["extra_hosts"] == frozenset({"eurointegration.com.ua"})


def test_discover_archive_isolates_unexpected_source_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    rows = [
        SimpleNamespace(
            source_id=source_id,
            tier=2,
            usable=True,
            tier_block=False,
            discovery_method="rss",
            feed_url=f"https://{source_id}.example/rss",
        )
        for source_id in ("bbc", "guardian")
    ]
    seen: list[str] = []

    def fake_discover(row: Any, feed_url: str, **kwargs: Any) -> ArchiveResult:
        seen.append(row.source_id)
        if row.source_id == "bbc":
            raise RuntimeError("unexpected parser failure")
        return ArchiveResult(source_id=row.source_id, feed_url=feed_url, discovered_urls=1)

    monkeypatch.setattr(backfill, "_load_rows", lambda _settings: rows)
    monkeypatch.setattr("media_quotes.cli.common._load_source_configs", lambda _settings: {})
    monkeypatch.setattr("media_quotes.cli.common._load_topic_pack", lambda *args: None)
    monkeypatch.setattr("media_quotes.archive.discover_archive_documents", fake_discover)

    results = backfill._discover_archive(
        settings,
        _request(fetch_archive=True, max_archive_articles=3),
        SimpleNamespace(effective_end=date(2026, 11, 19)),
    )

    assert seen == ["bbc", "guardian"]
    assert len(results) == 2
    assert "unexpected parser failure" in results[0].errors[0]
    assert results[0].discovered_urls == 0
    assert results[1].discovered_urls == 1


def test_archive_article_cap_is_shared_across_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    rows = [
        SimpleNamespace(
            source_id=source_id,
            tier=2,
            usable=True,
            tier_block=False,
            discovery_method="rss",
            feed_url=f"https://{source_id}.example/rss",
        )
        for source_id in ("bbc", "guardian")
    ]
    limits: list[int] = []

    def fake_discover(row: Any, feed_url: str, **kwargs: Any) -> ArchiveResult:
        limits.append(kwargs["max_articles"])
        used = 1 if row.source_id == "bbc" else kwargs["max_articles"]
        return ArchiveResult(
            source_id=row.source_id,
            feed_url=feed_url,
            discovered_urls=used,
            stored_articles=used,
        )

    monkeypatch.setattr(backfill, "_load_rows", lambda _settings: rows)
    monkeypatch.setattr("media_quotes.cli.common._load_source_configs", lambda _settings: {})
    monkeypatch.setattr("media_quotes.cli.common._load_topic_pack", lambda *args: None)
    monkeypatch.setattr("media_quotes.archive.discover_archive_documents", fake_discover)

    backfill._discover_archive(
        settings,
        _request(fetch_archive=True, max_archive_articles=3),
        SimpleNamespace(effective_end=date(2026, 11, 19)),
    )

    assert limits == [3, 2]


def test_backfill_pipelines_disable_paid_judge_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_quotes.pipeline import run_daily_pipeline

    settings = _settings(tmp_path)
    proposals: list[dict[str, Any]] = []

    def fake_propose(records: Any, *, now: Any, judge: Any, **kwargs: Any) -> list[Any]:
        proposals.append(kwargs)
        return []

    monkeypatch.setattr(
        "media_quotes.risk_controls.obsolescence.propose_obsolescence", fake_propose
    )
    monkeypatch.setattr(
        "media_quotes.cli.run._collect", lambda *args, **kwargs: ([], [], [], [], 0, {})
    )

    for pipeline in ("mq-backfill", "dagster-backfill"):
        run_daily_pipeline(
            settings,
            report_date=date(2026, 11, 18),
            pipeline=pipeline,
            source_mode="fixtures",
        )
    assert proposals == [{"max_decisions": 0}, {"max_decisions": 0}]

    run_daily_pipeline(
        settings, report_date=date(2026, 11, 18), pipeline="mq-run", source_mode="fixtures"
    )
    assert proposals[-1] == {}


def test_estimate_only_persists_through_injected_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path).model_copy(update={"persist_to_postgres": True})
    session = MagicMock()
    calls: list[str] = []

    @contextmanager
    def fake_context(_settings: Settings) -> Any:
        yield session

    monkeypatch.setattr(
        "media_quotes.db.repository.start_pipeline_run",
        lambda *args, **kwargs: calls.append("start"),
    )
    monkeypatch.setattr(
        "media_quotes.db.repository.finish_pipeline_run",
        lambda *args, **kwargs: calls.append("finish"),
    )

    result = backfill.execute_backfill(
        settings,
        _request(execute=False),
        session_context=fake_context,
    )

    assert calls == ["start", "finish"]
    assert result.estimate_run_id == "backfill-2026-11-18-2026-11-19"
    assert not result.partitions


def test_plan_rejects_unknown_source_mode_and_execute_without_budget(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(ConfigError, match="source_mode"):
        backfill.plan_backfill(settings, _request(source_mode="bogus"))
    with pytest.raises(ConfigError, match="max-cost"):
        backfill.plan_backfill(settings, _request(execute=True, max_cost=None))


def test_cached_hashes_skips_blank_malformed_and_non_string_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    export_file = tmp_path / "quotes.jsonl"
    export_file.write_text(
        '\nnot-json\n{"content_hash": "abc"}\n{"content_hash": 123}\n[]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(backfill, "local_store_path", lambda *args, **kwargs: export_file)

    assert backfill._cached_hashes(settings, _request(), 1) == 1


def test_backfill_result_aggregates_archive_stats() -> None:
    archives = [
        ArchiveResult(source_id="a", feed_url="u", stored_articles=2, errors=["e1"]),
        ArchiveResult(source_id="b", feed_url="u", stored_articles=3, errors=["e2", "e3"]),
    ]
    result = backfill.BackfillResult(plan=None, archives=archives)  # type: ignore[arg-type]
    assert result.archive_errors == 3
    assert result.archive_stored_articles == 5


def test_sync_backfill_graph_skips_when_graph_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("dagster")
    from media_quotes.orchestration.backfill_range import _sync_backfill_graph

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("no engine when graph sync is disabled")

    monkeypatch.setattr("media_quotes.db.engine.create_db_engine", forbidden)
    settings = _settings(tmp_path).model_copy(
        update={"persist_to_postgres": True, "sync_to_neo4j": False}
    )
    metadata = _sync_backfill_graph(
        SimpleNamespace(log=MagicMock()), settings, "ukraine-war", MagicMock()
    )
    assert metadata == {"neo4j_sync_status": "skipped", "neo4j_skip_reason": "graph sync disabled"}


def test_sync_backfill_graph_projects_stats_and_disposes_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from contextlib import contextmanager

    pytest.importorskip("dagster")
    from media_quotes.orchestration.backfill_range import _sync_backfill_graph

    settings = _settings(tmp_path).model_copy(
        update={"persist_to_postgres": True, "sync_to_neo4j": True}
    )
    engine = MagicMock()
    disposed: list[MagicMock] = []
    engine.dispose.side_effect = lambda: disposed.append(engine)
    session = object()
    neo4j = MagicMock()
    driver = MagicMock()
    neo4j.get_driver.return_value = driver

    @contextmanager
    def fake_graph_session(fake_driver: Any, database: str) -> Any:
        assert fake_driver is driver
        assert database == settings.neo4j_database
        yield session

    engine_calls: list[Any] = []

    def fake_engine(settings_arg: Any) -> Any:
        engine_calls.append(settings_arg)
        return engine

    monkeypatch.setattr("media_quotes.db.engine.create_db_engine", fake_engine)
    monkeypatch.setattr("media_quotes.graph.graph_session", fake_graph_session)
    monkeypatch.setattr("media_quotes.graph.init_schema", lambda _session: [])
    monkeypatch.setattr(
        "media_quotes.graph.sync_postgres_to_graph",
        lambda *_args, **_kwargs: SimpleNamespace(
            quotes_projected=7,
            articles_projected=3,
            speakers_projected=2,
            last_synced_at="2026-11-24T00:00:00+00:00",
            errors=[],
        ),
    )
    context = SimpleNamespace(log=MagicMock())

    metadata = _sync_backfill_graph(context, settings, "ukraine-war", neo4j)

    assert metadata["neo4j_sync_status"] == "ok"
    assert metadata["neo4j_quotes_projected"] == 7
    assert metadata["neo4j_articles_projected"] == 3
    assert metadata["neo4j_speakers_projected"] == 2
    assert metadata["neo4j_last_synced_at"] == "2026-11-24T00:00:00+00:00"
    assert metadata["neo4j_errors"] == []
    assert engine_calls == [settings]
    assert disposed == [engine]
    neo4j.get_driver.assert_called_once_with()


def test_range_helper_rejects_unsafe_config(tmp_path: Path) -> None:
    dg = pytest.importorskip("dagster")

    from media_quotes.orchestration.backfill_range import _run_backfill_range
    from media_quotes.orchestration.common import DailyPipelineConfig

    config = DailyPipelineConfig(source_mode="fixtures", max_cost=100.0)
    with pytest.raises(ConfigError, match="source_mode"):
        _run_backfill_range(
            SimpleNamespace(log=MagicMock(), add_output_metadata=MagicMock()),
            config,
            _settings(tmp_path),
            MagicMock(),
            MagicMock(),
            dg.PartitionKeyRange(start="2026-11-18", end="2026-11-19"),
        )
