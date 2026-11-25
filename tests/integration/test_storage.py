"""Integration tests for storage bootstrap, schema application and DB writes."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import Engine

from media_quotes.cli.run import _collect
from media_quotes.config import Settings
from media_quotes.db.engine import make_session_factory, session_scope
from media_quotes.db.repository import (
    DecisionCacheEntry,
    _run_uuid,
    append_audit_events,
    finish_pipeline_run,
    get_decision_cache,
    persist_articles_and_quotes,
    persist_digest_manifest,
    persist_obsolescence_flags,
    persist_quote_classifications,
    read_export_records,
    record_eval_run,
    start_pipeline_run,
    sync_gold_set_labels,
    sync_sources,
    upsert_decision_cache,
)
from media_quotes.db.tables import (
    articles_table,
    audit_events_table,
    digests_table,
    eval_runs_table,
    gold_set_labels_table,
    obsolescence_flags_table,
    pipeline_runs_table,
    quote_classifications_table,
    quotes_table,
)
from media_quotes.gold_set import GoldArticle, GoldQuoteLabel, GoldSet
from media_quotes.registry import SourceRegistryRow, load_sources_csv
from media_quotes.risk_controls.models import ObsolescenceEvidence, ObsolescenceFlag


def test_uuid_backed_columns_use_uuid_types() -> None:
    columns = [
        pipeline_runs_table.c.run_id,
        articles_table.c.article_id,
        quotes_table.c.quote_id,
        quotes_table.c.article_id,
        quote_classifications_table.c.classification_id,
        quote_classifications_table.c.quote_id,
        obsolescence_flags_table.c.flag_id,
        obsolescence_flags_table.c.quote_id,
        digests_table.c.digest_id,
        eval_runs_table.c.eval_id,
        audit_events_table.c.run_id,
        gold_set_labels_table.c.label_id,
    ]
    assert all(column.type.python_type is UUID for column in columns)
    assert isinstance(_run_uuid("run-1"), UUID)


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCES_CSV = REPO_ROOT / "data" / "sources.csv"
SCHEMA_SQL = REPO_ROOT / "src" / "media_quotes" / "db" / "schema.sql"


def test_schema_sql_has_t0_constraints() -> None:
    ddl = SCHEMA_SQL.read_text(encoding="utf-8")
    assert "CONSTRAINT sources_t0_gate CHECK" in ddl
    assert "CONSTRAINT sources_state_controlled_claim_only CHECK" in ddl
    assert "CONSTRAINT sources_tier4_claim_only CHECK" in ddl
    assert "rights_status IN ('license_held','api_permitted')" in ddl


def test_session_factory_produces_session() -> None:
    mock_engine = MagicMock(spec=Engine)
    factory = make_session_factory(mock_engine)
    session = factory()
    assert session is not None
    session.close()


def test_session_scope_commits_on_success() -> None:
    session_mock = MagicMock()
    factory_mock = MagicMock(return_value=session_mock)
    with session_scope(factory_mock) as session:
        assert session is session_mock
    session_mock.commit.assert_called_once()
    session_mock.rollback.assert_not_called()
    session_mock.close.assert_called_once()


def test_session_scope_rolls_back_on_exception() -> None:
    session_mock = MagicMock()
    factory_mock = MagicMock(return_value=session_mock)
    with pytest.raises(RuntimeError, match="boom"), session_scope(factory_mock):
        raise RuntimeError("boom")
    session_mock.rollback.assert_called_once()
    session_mock.commit.assert_not_called()
    session_mock.close.assert_called_once()


def test_repository_methods_support_commit_false() -> None:
    session = MagicMock()
    eval_id = record_eval_run(
        session,
        gold_set_version="synthetic-v1",
        report_date=date(2026, 11, 19),
        metrics={"recall": 1.0},
        thresholds={"recall": 0.9},
        passed=True,
        commit=False,
    )
    assert len(eval_id) == 36
    session.execute.assert_called_once()
    session.commit.assert_not_called()


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
        negative_candidates=[],
    )
    inserted = sync_gold_set_labels(session, gold)
    assert inserted >= 1
    session.execute.assert_called_once()
    session.commit.assert_called_once()


def test_get_decision_cache_queries_cache_table() -> None:
    session = MagicMock()
    row_data = {
        "cache_key": "k1",
        "model_snapshot": "m1",
        "output": {"test": "val"},
    }
    session.execute.return_value.mappings.return_value.first.return_value = row_data

    result = get_decision_cache(session, "k1")
    assert result == row_data
    session.execute.assert_called_once()


def test_get_decision_cache_returns_none_when_missing() -> None:
    session = MagicMock()
    session.execute.return_value.mappings.return_value.first.return_value = None

    result = get_decision_cache(session, "k_missing")
    assert result is None
    session.execute.assert_called_once()


def test_persist_articles_and_quotes_requires_offsets() -> None:
    session = MagicMock()
    records = _collect(Settings(), run_id="test", report_date=date(2026, 11, 19))[0]
    inserted_articles, inserted_quotes = persist_articles_and_quotes(
        session, records, offsets={}, extractor_version="fixture-replay-v1"
    )
    assert inserted_articles == 0
    assert inserted_quotes == 0
    session.execute.assert_not_called()
    session.commit.assert_called_once()


def test_persist_articles_and_quotes_persists_when_offsets_present() -> None:
    session = MagicMock()
    session.execute.return_value.rowcount = 1
    records = _collect(Settings(), run_id="test", report_date=date(2026, 11, 19))[0]
    first = records[0]
    offsets = {
        first.quote_id: {
            "char_start": 0,
            "char_end": len(first.quote_text_original),
            "content_hash": first.content_hash,
            "raw_blob_ref": "fixture://ap/synthetic-ap-2026-11-18.html",
            "extracted_text": first.quote_text_original,
            "extraction_method": "fixture_html_v1",
        }
    }
    inserted_articles, inserted_quotes = persist_articles_and_quotes(
        session, records, offsets=offsets, extractor_version="fixture-replay-v1"
    )
    assert inserted_articles >= 0
    assert inserted_quotes >= 0
    # Two INSERTs plus the canonical-id collision lookup SELECT.
    assert session.execute.call_count >= 2
    assert session.commit.call_count == 1


def test_quote_insert_writes_topic_and_translation_columns() -> None:
    from sqlalchemy.dialects import postgresql

    session = MagicMock()
    session.execute.return_value.rowcount = 1
    records = _collect(Settings(), run_id="test", report_date=date(2026, 11, 19))[0]
    first = records[0]
    offsets = {
        first.quote_id: {
            "char_start": 0,
            "char_end": len(first.quote_text_original),
            "content_hash": first.content_hash,
        }
    }
    persist_articles_and_quotes(session, records, offsets=offsets)
    quote_insert = str(
        session.execute.call_args_list[-1].args[0].compile(dialect=postgresql.dialect())
    )
    for column in (
        "topic_id",
        "topic_status",
        "topic_rule_version",
        "topic_score",
        "matched_terms",
        "quote_language",
        "language_confidence",
        "quote_text_en",
        "translation_model_snapshot",
        "translation_prompt_version",
        "translation_hash",
        "translation_status",
    ):
        assert column in quote_insert


def test_persist_articles_and_quotes_handles_psycopg_minus_one_rowcount() -> None:
    session = MagicMock()
    session.execute.return_value.rowcount = -1  # psycopg INSERT .. ON CONFLICT DO NOTHING
    records = _collect(Settings(), run_id="test", report_date=date(2026, 11, 19))[0]
    first = records[0]
    offsets = {
        first.quote_id: {
            "char_start": 0,
            "char_end": len(first.quote_text_original),
            "content_hash": first.content_hash,
        }
    }
    articles, quotes = persist_articles_and_quotes(session, records, offsets)
    assert articles == 1  # attempted insert count, not -1 -> 0
    assert quotes >= 0


def test_persist_quote_classifications_and_digest_manifest_and_obsolescence_flags() -> None:
    session = MagicMock()
    records, _, _, _, _, offsets = _collect(
        Settings(), run_id="test", report_date=date(2026, 11, 19)
    )
    first = records[0]
    detail = offsets[first.quote_id]
    article_id = _run_uuid(f"article:{first.article_url}")
    quote_uuid = _run_uuid(
        f"quote:{article_id}:{int(detail['char_start'])}:{int(detail['char_end'])}"
    )

    session.execute.return_value.scalars.return_value.all.return_value = [quote_uuid]
    session.execute.return_value.rowcount = 1
    classifications_n = persist_quote_classifications(session, [first], offsets, assigned_by="rule")
    assert classifications_n == 1

    with pytest.raises(ValueError, match="invalid assigned_by"):
        persist_quote_classifications(session, [first], offsets, assigned_by="invalid")

    digest_id = persist_digest_manifest(
        session,
        digest_date=date(2026, 11, 19),
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        md_path="digests/2026-11-19.md",
        records=[first],
    )
    assert len(digest_id) == 36

    flag = ObsolescenceFlag(
        evidence=ObsolescenceEvidence(
            old_quote_id=first.quote_id,
            old_content_hash="a" * 64,
            newer_article_id="art-new-1",
            newer_content_hash="b" * 64,
            newer_is_first_hand=True,
            newer_rights_cleared=True,
            superseding_span="Newer statement superseding old fact.",
            decision_id="dec-obs-1",
            rationale="Superseded by newer official statement.",
        ),
        confidence=0.95,
        raised_at=datetime(2026, 11, 19, 12, 0, tzinfo=UTC),
        state="confirmed",
        reason_code="superseded",
    )
    session.execute.return_value.all.return_value = [(first.quote_id, quote_uuid)]
    flags_n = persist_obsolescence_flags(session, [flag])
    assert flags_n == 1
    assert persist_obsolescence_flags(session, []) == 0


def test_read_export_records_keeps_one_row_per_quote() -> None:
    base = dict(
        q_id="Q-20261119-0001",
        q_text="A long enough synthetic quote for this export dedupe test.",
        q_text_en="",
        q_char_start=0,
        q_meta={},
        q_details={},
        q_method="m",
        q_topic="ukraine-war",
        q_topic_status="accepted",
        q_rule_version="",
        q_score=0.0,
        q_terms=[],
        q_language="en",
        q_lang_conf=0.0,
        q_t_model="",
        q_t_prompt="",
        q_t_hash="",
        q_t_status="",
        q_provenance="live_feed",
        a_url="https://example.invalid/a",
        a_title="Article",
        a_language="en",
        a_date=date(2026, 11, 19),
        a_meta={},
        a_hash="a" * 64,
        a_provenance="fixture",
        s_id="guardian",
        s_name="Guardian",
        s_tier=1,
        s_region="UK",
        s_type="publicly funded",
        s_rights="license_held",
        s_access="licensed_database",
        s_license="LIC-1",
        s_discovery="licensed_database",
        s_claim=False,
        s_language="en",
        o_reason="pending",
        o_created_at=datetime(2026, 11, 19, 10, 0, tzinfo=UTC),
    )
    rows = [
        {**base, "o_obsolete": False},
        {
            **base,
            "o_obsolete": True,
            "o_reason": "confirmed",
            "o_created_at": datetime(2026, 11, 19, 12, 0, tzinfo=UTC),
        },
    ]
    session = MagicMock()
    session.execute.return_value.mappings.return_value = rows
    records = read_export_records(session, report_date=date(2026, 11, 19), window_days=14)
    assert len(records) == 1
    assert records[0].is_obsolete is True
    assert records[0].obsolete_reason == "confirmed"
    assert records[0].provenance == "live_feed"


def test_read_export_records_defaults_legacy_provenance() -> None:
    from datetime import date as _date

    rows = [
        {
            "q_id": "Q-20261119-0001",
            "q_text": "A long enough synthetic quote for this export dedupe test.",
            "q_text_en": "",
            "q_char_start": 0,
            "q_meta": {},
            "q_details": {},
            "q_method": "m",
            "q_topic": "ukraine-war",
            "q_topic_status": "accepted",
            "q_rule_version": "",
            "q_score": 0.0,
            "q_terms": [],
            "q_language": "en",
            "q_lang_conf": 0.0,
            "q_t_model": "",
            "q_t_prompt": "",
            "q_t_hash": "",
            "q_t_status": "",
            "a_url": "https://example.invalid/a",
            "a_title": "Article",
            "a_language": "en",
            "a_date": _date(2026, 11, 19),
            "a_meta": {},
            "a_hash": "a" * 64,
            "s_id": "guardian",
            "s_name": "Guardian",
            "s_tier": 1,
            "s_region": "UK",
            "s_type": "publicly funded",
            "s_rights": "license_held",
            "s_access": "licensed_database",
            "s_license": "LIC-1",
            "s_discovery": "licensed_database",
            "s_claim": False,
            "s_language": "en",
            "o_obsolete": False,
            "o_reason": "",
            "o_created_at": None,
        }
    ]
    session = MagicMock()
    session.execute.return_value.mappings.return_value = rows
    records = read_export_records(session, report_date=_date(2026, 11, 19), window_days=14)
    assert records[0].provenance == "fixture"


def test_read_export_records_round_trips_and_filters_quote_provenance() -> None:
    from sqlalchemy.dialects import postgresql

    base = dict(
        q_text="A long enough synthetic quote for this export dedupe test.",
        q_text_en="",
        q_char_start=0,
        q_meta={},
        q_details={},
        q_method="m",
        q_topic="ukraine-war",
        q_topic_status="accepted",
        q_rule_version="",
        q_score=0.0,
        q_terms=[],
        q_language="en",
        q_lang_conf=0.0,
        q_t_model="",
        q_t_prompt="",
        q_t_hash="",
        q_t_status="",
        a_url="https://example.invalid/a",
        a_title="Article",
        a_language="en",
        a_date=date(2026, 11, 19),
        a_meta={},
        a_hash="a" * 64,
        s_id="guardian",
        s_name="Guardian",
        s_tier=1,
        s_region="UK",
        s_type="publicly funded",
        s_rights="license_held",
        s_access="licensed_database",
        s_license="LIC-1",
        s_discovery="licensed_database",
        s_claim=False,
        s_language="en",
        o_obsolete=False,
        o_reason="",
        o_created_at=None,
    )
    # Quote provenance wins over article provenance; the fallback path is also covered.
    rows = [
        {**base, "q_id": "Q-20261119-0001", "q_provenance": "fixture", "a_provenance": "live_feed"},
        {**base, "q_id": "Q-20261119-0002", "a_provenance": "live_bing"},
    ]
    session = MagicMock()
    session.execute.return_value.mappings.return_value = rows
    records = read_export_records(session, report_date=date(2026, 11, 19), window_days=14)
    assert [record.provenance for record in records] == ["fixture", "live_bing"]

    # A live-filtered read must add a quotes.provenance predicate and return only those rows.
    filtered_session = MagicMock()
    filtered_session.execute.return_value.mappings.return_value = [rows[1]]
    live_records = read_export_records(
        filtered_session, report_date=date(2026, 11, 19), window_days=14, provenance="live_feed"
    )
    assert [record.provenance for record in live_records] == ["live_bing"]
    statement = filtered_session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "quotes.provenance = %(provenance_1)s" in str(compiled)
    assert compiled.params["provenance_1"] == "live_feed"


def test_digest_manifests_coexist_across_provenance() -> None:
    from sqlalchemy.dialects import postgresql

    session = MagicMock()
    records = _collect(Settings(), run_id="test", report_date=date(2026, 11, 19))[0][:1]
    common = dict(
        digest_date=date(2026, 11, 19),
        window_start=date(2026, 11, 13),
        window_end=date(2026, 11, 19),
        md_path="digests/2026-11-19.md",
        records=records,
        topic_id="ukraine-war",
    )
    fixture_id = persist_digest_manifest(session, provenance="fixture", **common)
    live_id = persist_digest_manifest(session, provenance="live_feed", **common)
    assert fixture_id != live_id

    compiled = [
        call.args[0].compile(dialect=postgresql.dialect())
        for call in session.execute.call_args_list
    ]
    assert len(compiled) == 2
    assert [item.params["provenance"] for item in compiled] == ["fixture", "live_feed"]
    assert [item.params["manifest"]["provenance"] for item in compiled] == [
        "fixture",
        "live_feed",
    ]
    for item in compiled:
        assert "ON CONFLICT (topic_id, digest_date, provenance)" in str(item)


def test_pipeline_run_lifecycle() -> None:
    session = MagicMock()
    run_date = date(2026, 11, 19)
    run_uuid = start_pipeline_run(
        session,
        run_id="mq-2026-11-19-offline",
        pipeline="offline_replay",
        partition_date=run_date,
    )
    assert len(run_uuid) == 36
    session.execute.assert_called_once()
    session.commit.assert_called_once()

    finish_pipeline_run(
        session,
        run_id="mq-2026-11-19-offline",
        status="succeeded",
        stats={"quotes_published": 1},
    )
    assert session.execute.call_count == 2
    assert session.commit.call_count == 2

    with pytest.raises(ValueError, match="invalid terminal run status"):
        finish_pipeline_run(
            session,
            run_id="mq-2026-11-19-offline",
            status="invalid_status",
            stats={},
        )


def test_append_audit_events_persists_rows() -> None:
    session = MagicMock()
    assert append_audit_events(session, []) == 0
    session.execute.assert_not_called()

    events = [
        {
            "run_id": "mq-run-1",
            "step": "t0_rights_gate",
            "status": "blocked",
            "source_id": "reuters",
            "detail": "commercial license held=False",
        }
    ]
    inserted = append_audit_events(session, events)
    assert inserted == 1
    session.execute.assert_called_once()
    session.commit.assert_called_once()


def test_upsert_decision_cache_is_idempotent() -> None:
    with pytest.raises(ValidationError):
        DecisionCacheEntry(
            cache_key="",
            model_snapshot="m1",
            prompt_version="p1",
            schema_hash="s1",
            input_hash="i1",
            output={},
        )

    entry = DecisionCacheEntry(
        cache_key="k1",
        model_snapshot="m1",
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


def test_article_upsert_sql_guards_fixture_overwrite() -> None:
    from sqlalchemy.dialects import postgresql

    session = MagicMock()
    session.execute.return_value.rowcount = 1
    records = _collect(Settings(), run_id="test", report_date=date(2026, 11, 19))[0]
    first = records[0]
    offsets = {
        first.quote_id: {
            "char_start": 0,
            "char_end": len(first.quote_text_original),
            "content_hash": first.content_hash,
        }
    }
    persist_articles_and_quotes(session, [first], offsets)
    compiled = [
        str(call.args[0].compile(dialect=postgresql.dialect())).lower()
        for call in session.execute.call_args_list
    ]
    article_sql = next(sql for sql in compiled if "on conflict" in sql)
    assert "excluded.provenance" in article_sql
    assert (
        "articles.provenance = excluded.provenance or articles.provenance = "
        "%(provenance_1)s::varchar and excluded.provenance != %(provenance_2)s::varchar"
    ) in article_sql


def test_fixture_quote_spans_are_skipped_for_live_articles() -> None:
    """A fixture replay must not add quote spans to an article that is already live."""
    from sqlalchemy.sql import Insert, Select

    class ReplayResult:
        def __init__(self, rows: list[tuple[object, ...]]) -> None:
            self.rows = rows
            self.rowcount = 1

        def all(self) -> list[tuple[object, ...]]:
            return self.rows

        def __iter__(self):
            return iter(self.rows)

        def scalars(self) -> ReplayResult:
            return self

    class ReplaySession:
        def __init__(self, provenance_by_article: dict[UUID, str]) -> None:
            self.provenance_by_article = provenance_by_article
            self.executed: list[object] = []
            self.commits = 0

        def execute(self, statement, *args, **kwargs) -> ReplayResult:
            self.executed.append(statement)
            if isinstance(statement, Select):
                if {column.key for column in statement.selected_columns} & {"provenance"}:
                    return ReplayResult(list(self.provenance_by_article.items()))
                return ReplayResult([])
            return ReplayResult([])

        def commit(self) -> None:
            self.commits += 1

    records = _collect(Settings(), run_id="test", report_date=date(2026, 11, 19))[0]
    fixture = records[0]
    article_id = _run_uuid(f"article:{fixture.article_url}")
    offsets: dict[str, dict[str, object]] = {
        fixture.quote_id: {
            "char_start": 0,
            "char_end": len(fixture.quote_text_original),
            "content_hash": fixture.content_hash,
        }
    }

    live_session = ReplaySession({article_id: "live_feed"})
    _, quotes = persist_articles_and_quotes(live_session, [fixture], offsets)
    assert [stmt.table.name for stmt in live_session.executed if isinstance(stmt, Insert)] == [
        "articles"
    ]
    assert quotes == 0

    fixture_session = ReplaySession({article_id: "fixture"})
    _, quotes = persist_articles_and_quotes(fixture_session, [fixture], offsets)
    assert [stmt.table.name for stmt in fixture_session.executed if isinstance(stmt, Insert)] == [
        "articles",
        "quotes",
    ]
    assert quotes == 1


@pytest.mark.integration
def test_article_provenance_upgrade_is_one_way() -> None:
    """Needs a real Postgres: set MQ_TEST_DATABASE_URL to run this test."""
    import os

    from sqlalchemy import create_engine, func, select, text
    from sqlalchemy.orm import Session

    from media_quotes.db.repository import persist_digest_manifest
    from media_quotes.db.tables import (
        articles_table,
        digests_table,
        metadata,
        quotes_table,
        sources_table,
    )

    url = os.environ.get("MQ_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set MQ_TEST_DATABASE_URL to run the Postgres provenance test")
    schema = "mq_provenance_test"
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {schema}"))
            conn.execute(text(f"SET search_path TO {schema}, public"))
            metadata.create_all(
                conn, tables=[sources_table, articles_table, quotes_table, digests_table]
            )
            conn.execute(
                text(
                    "INSERT INTO sources (source_id, source_name, homepage_url, tier, "
                    "cadence_hours, country_region, source_type, claim_only, language, "
                    "requires_translation, rights_status, access_mode, discovery_method, "
                    "archive_start_date) VALUES "
                    "('guardian','Guardian','https://example.invalid',1,24,'UK',"
                    "'publicly funded',false,'en',false,'license_held','licensed_database',"
                    "'licensed_database',DATE '2026-01-01')"
                )
            )

        session = Session(engine)
        try:
            session.execute(text(f"SET search_path TO {schema}, public"))
            records = _collect(Settings(), run_id="test", report_date=date(2026, 11, 19))[0]
            fixture = records[0]
            live = fixture.model_copy(update={"provenance": "live_feed"})

            def _offsets(text_value: str) -> dict[str, dict[str, object]]:
                return {
                    fixture.quote_id: {
                        "char_start": 0,
                        "char_end": len(fixture.quote_text_original),
                        "content_hash": fixture.content_hash,
                        "extracted_text": text_value,
                    }
                }

            persist_articles_and_quotes(session, [fixture], _offsets("fixture text"))
            persist_articles_and_quotes(session, [live], _offsets("LIVE TEXT"))
            row = session.execute(
                text("SELECT extracted_text, provenance FROM articles WHERE canonical_url=:url"),
                {"url": fixture.article_url},
            ).one()
            assert row.provenance == "live_feed"
            assert row.extracted_text == "LIVE TEXT"

            persist_articles_and_quotes(session, [fixture], _offsets("fixture text"))
            row = session.execute(
                text("SELECT extracted_text, provenance FROM articles WHERE canonical_url=:url"),
                {"url": fixture.article_url},
            ).one()
            assert row.provenance == "live_feed"
            assert row.extracted_text == "LIVE TEXT"

            # A fixture span that would otherwise be new must not land on the live article.
            live_article_id = _run_uuid(f"article:{fixture.article_url}")

            def count_quotes() -> int:
                return int(
                    session.execute(
                        select(func.count())
                        .select_from(quotes_table)
                        .where(quotes_table.c.article_id == live_article_id)
                    ).scalar()
                    or 0
                )

            assert count_quotes() == 1

            extra_fixture = fixture.model_copy(update={"quote_id": "Q-20261119-0099"})
            extra_offsets = {
                extra_fixture.quote_id: {
                    "char_start": 3,
                    "char_end": 3 + len(extra_fixture.quote_text_original),
                    "content_hash": extra_fixture.content_hash,
                    "extracted_text": "fixture text",
                }
            }
            persist_articles_and_quotes(session, [extra_fixture], extra_offsets)
            assert count_quotes() == 1

            # The pre-existing quote keeps its fixture tag after the article upgrade;
            # quote-level provenance is what lets live-filtered reads exclude it.
            assert [
                row[0]
                for row in session.execute(
                    select(quotes_table.c.provenance).where(
                        quotes_table.c.article_id == live_article_id
                    )
                ).all()
            ] == ["fixture"]

            # Fixture and live digests for the same topic/date coexist.
            digest_common = dict(
                digest_date=date(2026, 11, 19),
                window_start=date(2026, 11, 13),
                window_end=date(2026, 11, 19),
                md_path="digests/2026-11-19.md",
                records=[fixture],
                topic_id="ukraine-war",
            )
            fixture_digest = persist_digest_manifest(session, provenance="fixture", **digest_common)
            live_digest = persist_digest_manifest(session, provenance="live_feed", **digest_common)
            assert fixture_digest != live_digest
            assert (
                session.execute(
                    select(func.count())
                    .select_from(digests_table)
                    .where(digests_table.c.digest_date == date(2026, 11, 19))
                ).scalar()
                == 2
            )
            session.commit()
        finally:
            session.close()
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        engine.dispose()


def test_sync_sources_with_empty_rows() -> None:
    session = MagicMock()
    assert sync_sources(session, []) == 0
    session.execute.assert_not_called()
    session.commit.assert_not_called()


def test_observed_audit_months_and_sync_storage_partitions() -> None:
    from sqlalchemy.sql import Insert, Select

    from media_quotes.db.repository import observed_audit_months, sync_storage_partitions

    class _Result:
        rowcount = -1  # psycopg ON CONFLICT DO NOTHING -> fall back to attempted count

        def __init__(self, months: list[datetime]) -> None:
            self._months = months

        def scalars(self) -> _Result:
            return self

        def all(self) -> list[datetime]:
            return self._months

    class _PartitionSession:
        def __init__(self, months: list[datetime]) -> None:
            self.months = months
            self.executed: list[object] = []
            self.commits = 0

        def execute(self, statement: object) -> _Result:
            self.executed.append(statement)
            return _Result(self.months if isinstance(statement, Select) else [])

        def commit(self) -> None:
            self.commits += 1

    months = [datetime(2026, 10, 1, 9, 0), datetime(2026, 11, 1, 9, 0)]
    session = _PartitionSession(months)
    assert observed_audit_months(session) == [date(2026, 10, 1), date(2026, 11, 1)]

    assert sync_storage_partitions(session) == 2
    inserts = [stmt for stmt in session.executed if isinstance(stmt, Insert)]
    assert len(inserts) == 1
    assert inserts[0].table.name == "storage_partitions"
    assert session.commits == 1

    empty = _PartitionSession([])
    assert observed_audit_months(empty) == []
    assert sync_storage_partitions(empty) == 0
    assert empty.commits == 0


@pytest.mark.integration
def test_provenance_downgrade_keeps_deterministic_survivors() -> None:
    """Needs a real Postgres: set MQ_TEST_DATABASE_URL to run this test."""
    import os
    from importlib.util import module_from_spec, spec_from_file_location

    from sqlalchemy import create_engine, text

    from media_quotes.db.tables import metadata

    url = os.environ.get("MQ_TEST_DATABASE_URL")
    if not url:
        pytest.skip("set MQ_TEST_DATABASE_URL to run the Postgres downgrade test")
    path = Path(__file__).resolve().parents[2] / "migrations/versions/0010_article_provenance.py"
    spec = spec_from_file_location("mq_0010_provenance", path)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    schema = "mq_downgrade_test"
    engine = create_engine(url)
    try:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            conn.execute(text(f"CREATE SCHEMA {schema}"))
            conn.execute(text(f"SET search_path TO {schema}, public"))
            metadata.create_all(conn, tables=[digests_table])
            rows = [
                ("t1", "00000000-0000-0000-0000-000000000001", "fixture"),
                ("t1", "00000000-0000-0000-0000-000000000002", "live_feed"),
                ("t2", "00000000-0000-0000-0000-000000000003", "live_bing"),
                ("t2", "00000000-0000-0000-0000-000000000004", "manual_sample"),
                ("t3", "00000000-0000-0000-0000-000000000005", "live_bing"),
                ("t3", "00000000-0000-0000-0000-000000000006", "live_feed"),
            ]
            for topic, digest_id, provenance in rows:
                conn.execute(
                    text(
                        "INSERT INTO digests (digest_id, digest_date, window_start, window_end, "
                        "md_path, topic_id, provenance) VALUES "
                        "(:digest_id, DATE '2026-11-19', DATE '2026-11-13', "
                        "DATE '2026-11-19', 'digests/d.md', :topic, :provenance)"
                    ),
                    {"digest_id": digest_id, "topic": topic, "provenance": provenance},
                )
            for statement in module._DOWNGRADE_DEDUPE_SQL:
                conn.execute(text(statement))
            survivors = {
                row[0]: row[1]
                for row in conn.execute(
                    text("SELECT topic_id, digest_id FROM digests ORDER BY topic_id")
                ).all()
            }
            assert survivors == {
                "t1": "00000000-0000-0000-0000-000000000002",  # live beats fixture
                "t2": "00000000-0000-0000-0000-000000000003",  # lowest digest_id wins
                "t3": "00000000-0000-0000-0000-000000000006",  # live_feed wins over lower id
            }
    finally:
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        engine.dispose()


def test_sync_sources_upserts_rows_without_database() -> None:
    rows = load_sources_csv(SOURCES_CSV)[:2]
    assert all(isinstance(row, SourceRegistryRow) for row in rows)
    session = MagicMock()

    assert sync_sources(session, rows) == len(rows)
    session.execute.assert_called_once()
    session.commit.assert_called_once()
