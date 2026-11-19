"""Integration tests for storage bootstrap, schema application and DB writes."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import Engine

from media_quotes.cli.common import _parse_report_date
from media_quotes.cli.run import _collect
from media_quotes.config import Settings
from media_quotes.db.engine import make_session_factory
from media_quotes.db.repository import (
    DecisionCacheEntry,
    append_audit_events,
    finish_pipeline_run,
    get_decision_cache,
    persist_articles_and_quotes,
    record_eval_run,
    start_pipeline_run,
    sync_gold_set_labels,
    sync_sources,
    upsert_decision_cache,
)
from media_quotes.gold_set import GoldArticle, GoldQuoteLabel, GoldSet
from media_quotes.registry import SourceRegistryRow, load_sources_csv

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_CSV = REPO_ROOT / "data" / "sources.csv"
SCHEMA_SQL = REPO_ROOT / "src" / "media_quotes" / "db" / "schema.sql"


def test_schema_sql_has_t0_constraints() -> None:
    ddl = SCHEMA_SQL.read_text(encoding="utf-8")
    assert "CONSTRAINT sources_t0_gate CHECK" in ddl
    assert "CONSTRAINT sources_state_controlled_claim_only CHECK" in ddl
    assert "CONSTRAINT sources_tier4_disabled CHECK" in ddl
    assert "rights_status IN ('license_held','api_permitted')" in ddl


def test_session_factory_produces_session() -> None:
    mock_engine = MagicMock(spec=Engine)
    factory = make_session_factory(mock_engine)
    session = factory()
    assert session is not None
    session.close()


def test_record_eval_run_is_idempotent() -> None:
    session = MagicMock()
    metrics = {"article_recall": 1.0, "quote_recall": 1.0, "false_quote_rate": 0.0}
    thresholds = {"article_recall": 0.90}
    run_date = date(2026, 11, 19)

    eval_id_1 = record_eval_run(
        session,
        gold_set_version="synthetic-v1",
        report_date=run_date,
        metrics=metrics,
        thresholds=thresholds,
        passed=True,
    )
    eval_id_2 = record_eval_run(
        session,
        gold_set_version="synthetic-v1",
        report_date=run_date,
        metrics=metrics,
        thresholds=thresholds,
        passed=True,
    )
    assert eval_id_1 == eval_id_2
    assert len(eval_id_1) == 36
    assert session.execute.call_count == 2
    assert session.commit.call_count == 2


def test_sync_gold_set_labels_persists_splits() -> None:
    session = MagicMock()
    session.execute.return_value.rowcount = 2
    gold = GoldSet(
        version="v1",
        articles=[
            GoldArticle(
                article_ref="fixture://reuters/art-1",
                source_id="reuters",
                publication_date="2026-11-18",
                relevant=True,
                quotes=[
                    GoldQuoteLabel(
                        quote_text="Quote text here",
                        section_id="war_intensity",
                        human_label=True,
                    )
                ],
            )
        ],
    )
    count = sync_gold_set_labels(session, gold)
    assert count >= 1
    session.execute.assert_called_once()
    session.commit.assert_called_once()


def test_get_decision_cache_queries_cache_key() -> None:
    session = MagicMock()
    session.execute.return_value.mappings.return_value.first.return_value = {
        "cache_key": "k1",
        "output": {"text": "hello"},
    }
    result = get_decision_cache(session, "k1")
    assert result is not None
    assert result["output"]["text"] == "hello"


def test_persist_articles_and_quotes_skips_missing_offsets() -> None:
    settings = Settings(_env_file=None)
    date_value = _parse_report_date(None, settings)
    records, _cov, _gaps, _events, _rejected, offsets = _collect(
        settings, run_id="run-persist-test", report_date=date_value
    )
    assert records and offsets
    session = MagicMock()
    session.execute.return_value.rowcount = len(records)
    articles, quotes = persist_articles_and_quotes(session, records, offsets)
    assert articles >= 1
    assert quotes == len(records)
    assert session.execute.call_count == 2
    session.commit.assert_called_once()

    session2 = MagicMock()
    assert persist_articles_and_quotes(session2, records, {}) == (0, 0)
    session2.execute.assert_not_called()


def test_pipeline_run_lifecycle_uses_stable_uuid() -> None:
    session = MagicMock()
    uuid = start_pipeline_run(
        session, run_id="run-abc123", pipeline="daily", partition_date=date(2026, 11, 19)
    )
    assert len(uuid) == 36
    assert (
        start_pipeline_run(
            session, run_id="run-abc123", pipeline="daily", partition_date=date(2026, 11, 19)
        )
        == uuid
    )
    finish_pipeline_run(session, run_id="run-abc123", status="partial", stats={"quotes": 2})
    with pytest.raises(ValueError):
        finish_pipeline_run(session, run_id="run-abc123", status="running", stats={})


def test_append_audit_events_is_append_only() -> None:
    session = MagicMock()
    events = [
        {
            "run_id": "run-abc123",
            "trace_id": "t1",
            "step": "discover",
            "status": "fixture_replay",
            "source_id": "reuters",
        },
        {"step": "coverage", "status": "blocked", "source_id": "bbc"},
    ]
    assert append_audit_events(session, events) == 2
    statement = session.execute.call_args.args[0]
    assert "INSERT INTO audit_events" in str(statement)
    assert append_audit_events(session, []) == 0
    session.commit.assert_called_once()


def test_decision_cache_entry_strict_model() -> None:
    entry = DecisionCacheEntry(
        cache_key="k",
        model_snapshot="snap",
        prompt_version="p1",
        schema_hash="s1",
        input_hash="i1",
        output={"quote_text": "x"},
    )
    assert entry.trace_id is None
    with pytest.raises(ValidationError):
        DecisionCacheEntry(
            cache_key="k",
            model_snapshot="snap",
            prompt_version="p1",
            schema_hash="s1",
            input_hash="i1",
            output="not-a-dict",
        )


def test_upsert_decision_cache_is_idempotent_without_overwrite() -> None:
    entry = DecisionCacheEntry(
        cache_key="k",
        model_snapshot="snap",
        prompt_version="p1",
        schema_hash="s1",
        input_hash="i1",
        output={"ok": True},
    )
    session = MagicMock()
    session.execute.return_value.rowcount = 1
    assert upsert_decision_cache(session, entry) is True
    statement = session.execute.call_args.args[0]
    # Idempotency is enforced at SQL level by ON CONFLICT DO NOTHING (no overwrite).
    assert "ON CONFLICT" in str(statement)
    session.commit.assert_called_once()

    session2 = MagicMock()
    session2.execute.return_value.rowcount = 0
    assert upsert_decision_cache(session2, entry) is False


def test_sync_sources_with_empty_rows() -> None:
    session = MagicMock()
    assert sync_sources(session, []) == 0
    session.execute.assert_not_called()
    session.commit.assert_not_called()


def test_sync_sources_upserts_rows_without_database() -> None:
    rows = load_sources_csv(SOURCES_CSV)[:2]
    assert all(isinstance(row, SourceRegistryRow) for row in rows)
    session = MagicMock()

    assert sync_sources(session, rows) == len(rows)
    session.execute.assert_called_once()
    session.commit.assert_called_once()
