"""Unit tests for Track B Neo4j derived graph projection features.

Covers:
1. Schema initialization (uniqueness constraints and indexes).
2. Pure deterministic projection from ExportQuoteRecord sequences.
3. Graph analytical queries (lineage, speaker timeline, divergence, GraphRAG context).
4. Graph-aware bounded context pack builder.
5. CLI subcommands (`mq graph init`, `sync`, `status`, `lineage`, `speaker`, `divergence`,
   `context`).
6. Dashboard graph projection summary.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from media_quotes import cli
from media_quotes.cli import graph as graph_cli
from media_quotes.cli.common import EXIT_FAILURE, EXIT_OK
from media_quotes.config import Settings
from media_quotes.context_pack import build_graph_context_pack
from media_quotes.dashboard import graph_projection_summary
from media_quotes.graph import (
    CONSTRAINTS,
    INDEXES,
    GraphSyncStats,
    Neo4jResourceHandle,
    check_graph_connectivity,
    fetch_cross_tier_divergence,
    fetch_graphrag_subgraph,
    fetch_speaker_timeline,
    fetch_supersession_lineage,
    fetch_sync_metadata,
    graph_session,
    init_schema,
    project_records_to_graph,
    sync_postgres_to_graph,
)
from media_quotes.models import (
    ExportQuoteRecord,
    VerificationRecord,
)


class MockResult:
    """Mock result cursor for GraphSessionProtocol."""

    def __init__(self, data: list[dict[str, Any]] | None = None) -> None:
        self._data = data or []

    def data(self) -> list[dict[str, Any]]:
        # Match the real driver: data() returns the remainder and consumes it.
        rows, self._data = self._data, []
        return rows

    def __iter__(self) -> Any:
        return iter(self._data)


class MockSession:
    """Mock in-memory session for GraphSessionProtocol."""

    def __init__(self, query_responses: Mapping[str, list[dict[str, Any]]] | None = None) -> None:
        self.queries: list[tuple[str, Mapping[str, Any] | None]] = []
        self._responses = dict(query_responses or {})

    def run(
        self, query: str, parameters: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> MockResult:
        self.queries.append((query, parameters))
        for key, res in self._responses.items():
            if key in query:
                return MockResult(res)
        return MockResult([])

    def close(self) -> None:
        pass


class MockDriver:
    """Mock in-memory driver for GraphDriverProtocol."""

    def __init__(self, session: MockSession | None = None) -> None:
        self._session = session or MockSession()
        self.connected = True
        self.close_calls = 0

    def session(self, **kwargs: Any) -> MockSession:
        return self._session

    def verify_connectivity(self) -> None:
        if not self.connected:
            raise ConnectionError("Neo4j unreachable")

    def close(self) -> None:
        self.close_calls += 1


def _make_sample_record(
    quote_id: str = "Q-20261123-0001",
    speaker: str = "Andrii Kovalenko",
    is_obsolete: bool = False,
    superseded_id: str | None = None,
) -> ExportQuoteRecord:
    rec = ExportQuoteRecord(
        quote_id=quote_id,
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
        publication_date=date(2026, 11, 23),
        article_name="Ukraine air defence update",
        article_author=speaker,
        article_url="https://www.theguardian.com/world/2026/nov/23/ukraine-air-defence",
        extraction_date=datetime(2026, 11, 23, 10, 0, 0),
        section_id="war_intensity",
        is_obsolete=is_obsolete,
        obsolete_reason="Newer delivery announcement" if is_obsolete else "",
        content_hash="a" * 64,
        verification=VerificationRecord(
            verbatim_match=True,
            metadata_verified=True,
            method="sha256-exact",
            verified_at=datetime(2026, 11, 23, 10, 5, 0),
        ),
        matched_terms=["air defence", "nato aid"],
        notes=f"supersedes: {superseded_id}" if superseded_id else "",
    )
    return rec


def test_init_schema_executes_all_constraints_and_indexes() -> None:
    session = MockSession()
    applied = init_schema(session)
    assert len(applied) == len(CONSTRAINTS) + len(INDEXES)
    assert len(session.queries) == len(applied)
    assert any("source_id_unique" in q[0] for q in session.queries)
    assert any("quote_id_unique" in q[0] for q in session.queries)
    assert any("speaker_key_unique" in q[0] for q in session.queries)


def test_project_records_to_graph_creates_nodes_and_relationships() -> None:
    session = MockSession()
    rec1 = _make_sample_record("Q-20261123-0001", speaker="Andrii Kovalenko")
    rec2 = _make_sample_record(
        "Q-20261123-0002",
        speaker="Andrii Kovalenko",
        is_obsolete=True,
        superseded_id="Q-20261123-0001",
    )

    stats = project_records_to_graph([rec1, rec2], session)
    assert stats.quotes_projected == 2
    assert stats.sources_projected == 1
    assert stats.articles_projected == 1
    assert stats.speakers_projected == 1
    assert not stats.errors

    # Verify speaker MERGE and term MERGE were executed
    queries_text = [q[0] for q in session.queries]
    assert any("MERGE (sp:Speaker" in q for q in queries_text)
    assert any("MERGE (tm:Term" in q for q in queries_text)
    assert any("MERGE (newer)-[sup:SUPERSEDES]->(older)" in q for q in queries_text)
    assert any("ProjectionMetadata" in q for q in queries_text)
    metadata_params = next(
        params for query, params in session.queries if "ProjectionMetadata" in query
    )
    assert metadata_params["quotes_projected"] == 2
    assert metadata_params["error_count"] == 0


def test_postgres_sync_casts_optional_report_date(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value = []
    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        "media_quotes.graph.sync.project_records_to_graph",
        lambda records, session: captured.update(records=records, session=session),
    )

    sync_postgres_to_graph(engine, MockSession(), report_date=None)

    statement = str(connection.execute.call_args.args[0])
    assert "CAST(:report_date AS DATE)" in statement
    assert "SELECT newer.canonical_quote_id" in statement
    assert captured["records"] == []


def test_projection_keeps_export_quote_hash_and_article_hash_apart() -> None:
    session = MockSession()
    record = {
        "quote_id": "Q-20261123-0001",
        "content_hash": "f" * 64,  # legacy fallback only
        "quote_content_hash": "e" * 64,  # quotes.quote_metadata->>'content_hash'
        "article_content_hash": "a" * 64,  # articles.content_hash
        "topic_id": "ukraine-war",
    }

    project_records_to_graph([record], session)

    quote_params = next(
        params for query, params in session.queries if "MERGE (q:Quote" in query and params
    )
    assert quote_params["quote_content_hash"] == "e" * 64
    assert quote_params["article_content_hash"] == "a" * 64
    assert "content_hash" not in quote_params
    assert "$article_content_hash" in next(
        query for query, _ in session.queries if "MERGE (a:Article" in query
    )


def test_legacy_postgres_mapping_gets_export_compatible_quote_hash() -> None:
    from media_quotes.export import content_hash

    session = MockSession()
    article_hash = "a" * 64
    record = {
        "quote_id": "Q-20261123-0001",
        "quote_text_raw": "Air defence deliveries will accelerate this winter.",
        "char_start": 12,
        "source_id": "guardian",
        "article_url": "https://www.theguardian.com/article",
        "article_content_hash": article_hash,
        # No quote_content_hash and no legacy content_hash, like pre-0007 Postgres rows.
    }

    project_records_to_graph([record], session)

    quote_params = next(
        params for query, params in session.queries if "MERGE (q:Quote" in query and params
    )
    expected = content_hash(
        [
            "guardian",
            "https://www.theguardian.com/article",
            "Air defence deliveries will accelerate this winter.",
            "12",
            article_hash,
        ]
    )
    assert quote_params["quote_content_hash"] == expected
    assert quote_params["article_content_hash"] == article_hash


def test_postgres_sync_selects_quote_and_article_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value = []
    monkeypatch.setattr(
        "media_quotes.graph.sync.project_records_to_graph", lambda records, session: None
    )

    sync_postgres_to_graph(engine, MockSession())

    statement = str(connection.execute.call_args.args[0])
    assert "q.char_start" in statement
    assert "q.quote_metadata->>'content_hash' AS quote_content_hash" in statement
    assert "a.content_hash AS article_content_hash" in statement


def test_postgres_superseded_by_points_from_newer_to_older() -> None:
    session = MockSession()
    older = _make_sample_record("Q-20261123-0001", is_obsolete=True).model_dump()
    older["superseded_by"] = ["Q-20261123-0002"]
    older["obsolete_reason"] = "Newer confirmed statement"
    newer = _make_sample_record("Q-20261123-0002").model_dump()

    stats = project_records_to_graph([older, newer], session)

    supersession = next(
        params for query, params in session.queries if "SUPERSEDES" in query and params
    )
    assert supersession == {
        "newer_id": "Q-20261123-0002",
        "older_id": "Q-20261123-0001",
        "reason": "Newer confirmed statement",
    }
    metadata_params = next(
        params for query, params in session.queries if "ProjectionMetadata" in query and params
    )
    assert stats.last_synced_at == metadata_params["last_synced_at"]


def test_neo4j_resource_handle_is_lazy_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = MockDriver()
    calls: list[Settings] = []
    monkeypatch.setattr(
        "media_quotes.graph.client.get_driver",
        lambda settings: calls.append(settings) or driver,
    )
    settings = Settings(_env_file=None)
    resource = Neo4jResourceHandle(settings)

    assert resource.get_driver() is driver
    assert resource.get_driver() is driver
    resource.close()
    resource.close()

    assert calls == [settings]
    assert driver.close_calls == 1


def test_fetch_sync_metadata_returns_latest_projection_summary() -> None:
    metadata = {
        "last_synced_at": "2026-11-24T09:00:00+00:00",
        "quotes_projected": 40,
        "error_count": 0,
    }
    session = MockSession({"ProjectionMetadata": [metadata]})

    assert fetch_sync_metadata(session) == metadata
    assert any("ProjectionMetadata" in query for query, _ in session.queries)


def test_fetch_sync_metadata_empty_when_no_sync_has_run() -> None:
    assert fetch_sync_metadata(MockSession()) == {}


def test_graph_analytical_queries() -> None:
    lineage_row = {
        "quote_id": "Q-20261123-0001",
        "quote_text": "Delivery will accelerate",
        "publication_date": "2026-11-23",
        "is_obsolete": False,
        "superseded_id": "Q-20261120-0001",
        "superseded_text": "Talks are exploratory",
        "supersession_reason": "Contract signed",
    }
    speaker_rows = [
        {
            "speaker": "Andrii Kovalenko",
            "quote_id": "Q-20261123-0001",
            "quote_text": "Delivery will accelerate",
            "publication_date": "2026-11-23",
            "section_id": "war_intensity",
            "source_id": "guardian",
            "source_tier": 2,
            "country_region": "United Kingdom",
            "is_obsolete": False,
        }
    ]
    divergence_rows = [
        {
            "tier": 1,
            "country_region": "United Kingdom",
            "claim_only": False,
            "section_id": "war_intensity",
            "quote_count": 5,
            "sources": ["reuters", "bbc"],
        }
    ]
    subgraph_rows = [
        {
            "quote_id": "Q-20261123-0001",
            "quote_text": "Delivery will accelerate",
            "quote_text_en": "Delivery will accelerate",
            "quote_language": "en",
            "publication_date": "2026-11-23",
            "article_url": "https://example.com/art1",
            "source_id": "guardian",
            "tier": 2,
            "country_region": "United Kingdom",
            "claim_only": False,
            "section_id": "war_intensity",
            "speaker": "Andrii Kovalenko",
            "supersedes_quote_id": "Q-20261120-0001",
            "supersession_reason": "Contract signed",
        }
    ]

    session = MockSession(
        {
            "canonical_quote_id: $quote_id": [lineage_row],
            "sp.speaker_key = $speaker_key": speaker_rows,
            "count(q) AS quote_count": divergence_rows,
            "q.is_obsolete = false": subgraph_rows,
        }
    )

    lineage = fetch_supersession_lineage(session, "Q-20261123-0001")
    assert lineage["quote_id"] == "Q-20261123-0001"
    assert lineage["superseded_id"] == "Q-20261120-0001"

    timeline = fetch_speaker_timeline(session, "Andrii Kovalenko")
    assert len(timeline) == 1
    assert timeline[0]["speaker"] == "Andrii Kovalenko"

    divergence = fetch_cross_tier_divergence(session, topic_id="ukraine-war")
    assert len(divergence) == 1
    assert divergence[0]["tier"] == 1

    subgraph = fetch_graphrag_subgraph(session, topic_id="ukraine-war")
    assert len(subgraph) == 1
    assert subgraph[0]["speaker"] == "Andrii Kovalenko"


def test_build_graph_context_pack() -> None:
    subgraph_quotes = [
        {
            "quote_id": "Q-20261123-0001",
            "quote_text": "Delivery will accelerate",
            "quote_text_en": "Delivery will accelerate",
            "source_id": "guardian",
            "tier": 2,
            "country_region": "United Kingdom",
            "claim_only": False,
            "section_id": "war_intensity",
            "speaker": "Andrii Kovalenko",
            "supersedes_quote_id": "Q-20261120-0001",
            "supersession_reason": "Contract signed",
        }
    ]
    pack = build_graph_context_pack(subgraph_quotes, max_tokens=1000)
    assert pack["manifest"]["generator_version"] == "graph-context-pack-v1"
    assert pack["manifest"]["quote_count"] == 1
    assert pack["quotes"][0]["speaker"] == "Andrii Kovalenko"
    assert pack["quotes"][0]["supersedes"]["quote_id"] == "Q-20261120-0001"

    with pytest.raises(ValueError, match="max_tokens must be positive"):
        build_graph_context_pack(subgraph_quotes, max_tokens=0)


def test_cli_graph_subcommands(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mock_session = MockSession(
        {
            "NOT n:ProjectionMetadata": [{"node_count": 10}],
            "count(r) AS rel_count": [{"rel_count": 15}],
            "SUPERSEDES": [{"quote_id": "Q-1", "quote_text": "T", "is_obsolete": False}],
            "sp.speaker_key": [{"speaker": "S1", "quote_id": "Q-1"}],
            "count(q) AS quote_count": [{"tier": 1, "quote_count": 3}],
            "q.is_obsolete = false": [{"quote_id": "Q-1", "quote_text": "Active"}],
        }
    )
    mock_driver = MockDriver(mock_session)
    monkeypatch.setattr("media_quotes.cli.graph.get_driver", lambda _: mock_driver)

    settings = Settings(
        _env_file=None,
        sync_to_neo4j=True,
        export_dir=tmp_path / "quotes",
        audit_dir=tmp_path / "audit",
    )
    monkeypatch.setattr("media_quotes.cli.app.get_settings", lambda: settings)

    assert cli.main(["graph", "init"]) == 0
    assert cli.main(["graph", "status"]) == 0
    assert cli.main(["graph", "lineage", "--quote-id", "Q-1"]) == 0
    assert cli.main(["graph", "speaker", "--name", "S1"]) == 0
    assert cli.main(["graph", "divergence"]) == 0
    assert cli.main(["graph", "context", "--max-tokens", "2000"]) == 0


def test_graph_sync_without_date_loads_full_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, export_dir=tmp_path / "quotes")
    monkeypatch.setattr("media_quotes.cli.app.get_settings", lambda: settings)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "media_quotes.cli.graph._do_graph_sync",
        lambda _settings, **kwargs: calls.append(kwargs) or 0,
    )

    assert cli.main(["graph", "sync", "--from-db"]) == 0
    assert calls[0]["report_date"] is None

    assert cli.main(["graph", "sync", "--from-db", "--date", "2026-11-24"]) == 0
    assert calls[1]["report_date"] == date(2026, 11, 24)


def test_dashboard_graph_projection_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. When sync_to_neo4j is False
    disabled_settings = Settings(_env_file=None, sync_to_neo4j=False)
    res_disabled = graph_projection_summary(disabled_settings)
    assert res_disabled["status"] == "disabled"

    # 2. When sync_to_neo4j is True and connected
    mock_session = MockSession(
        {
            "NOT n:ProjectionMetadata": [{"node_count": 42}],
            "count(r) AS rel_count": [{"rel_count": 84}],
            "ProjectionMetadata": [
                {
                    "last_synced_at": "2026-11-24T09:00:00+00:00",
                    "quotes_projected": 40,
                    "error_count": 0,
                }
            ],
        }
    )
    mock_driver = MockDriver(mock_session)
    monkeypatch.setattr("media_quotes.graph.get_driver", lambda _: mock_driver)

    enabled_settings = Settings(
        _env_file=None,
        sync_to_neo4j=True,
        neo4j_password=SecretStr("pass123"),
    )
    res_enabled = graph_projection_summary(enabled_settings)
    assert res_enabled["status"] == "connected"
    assert res_enabled["node_count"] == 42
    assert res_enabled["relationship_count"] == 84
    assert res_enabled["last_synced_at"] == "2026-11-24T09:00:00+00:00"
    assert res_enabled["quotes_projected"] == 40


class RaisingSession:
    """Session whose queries always fail, to exercise CLI exception branches."""

    def run(self, query: str, parameters: Any = None, **kwargs: Any) -> Any:
        raise RuntimeError("cypher boom")

    def close(self) -> None:
        pass


def test_graph_sync_reports_missing_export_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    driver = MockDriver()
    monkeypatch.setattr(graph_cli, "get_driver", lambda _settings: driver)
    missing = tmp_path / "missing.jsonl"
    monkeypatch.setattr(graph_cli, "local_store_path", lambda *args, **kwargs: missing)

    code = graph_cli._do_graph_sync(Settings(_env_file=None), report_date=date(2026, 11, 24))

    assert code == EXIT_FAILURE
    assert "export file not found" in capsys.readouterr().err
    assert driver.close_calls == 1


def test_graph_sync_fails_when_stats_have_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    export_file = tmp_path / "quotes.jsonl"
    export_file.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(graph_cli, "get_driver", lambda _settings: MockDriver())
    monkeypatch.setattr(graph_cli, "local_store_path", lambda *args, **kwargs: export_file)
    monkeypatch.setattr(graph_cli, "read_jsonl", lambda _path: [])
    monkeypatch.setattr(
        graph_cli,
        "project_records_to_graph",
        lambda _records, _session: GraphSyncStats(errors=["bad record"]),
    )

    code = graph_cli._do_graph_sync(Settings(_env_file=None))

    assert code == EXIT_FAILURE
    payload = capsys.readouterr().out
    assert "bad record" in payload


def test_graph_sync_from_db_closes_engine_and_driver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = MockDriver()
    engine = MagicMock()
    disposed: list[MagicMock] = []
    engine.dispose.side_effect = lambda: disposed.append(engine)
    monkeypatch.setattr(graph_cli, "get_driver", lambda _settings: driver)
    monkeypatch.setattr(graph_cli, "create_db_engine", lambda _settings: engine)
    monkeypatch.setattr(
        graph_cli, "sync_postgres_to_graph", lambda *_args, **_kwargs: GraphSyncStats()
    )

    code = graph_cli._do_graph_sync(Settings(_env_file=None), from_db=True)

    assert code == EXIT_OK
    assert disposed == [engine]
    assert driver.close_calls == 1


def test_graph_sync_exception_returns_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(_settings: Settings) -> Any:
        raise RuntimeError("driver unavailable")

    monkeypatch.setattr(graph_cli, "get_driver", fail)

    code = graph_cli._do_graph_sync(Settings(_env_file=None), from_db=True)

    assert code == EXIT_FAILURE
    assert "error syncing to graph: driver unavailable" in capsys.readouterr().err


def test_graph_status_unreachable_returns_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    driver = MockDriver()
    driver.connected = False
    monkeypatch.setattr(graph_cli, "get_driver", lambda _settings: driver)

    code = graph_cli._do_graph_status(Settings(_env_file=None))

    assert code == EXIT_FAILURE
    assert "unreachable" in capsys.readouterr().out
    assert driver.close_calls == 1


def test_graph_init_and_lineage_close_driver_on_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail_schema(_session: Any) -> Any:
        raise RuntimeError("schema boom")

    init_driver = MockDriver()
    monkeypatch.setattr(graph_cli, "get_driver", lambda _settings: init_driver)
    monkeypatch.setattr(graph_cli, "init_schema", fail_schema)
    assert graph_cli._do_graph_init(Settings(_env_file=None)) == EXIT_FAILURE
    assert "error initializing graph schema: schema boom" in capsys.readouterr().err
    assert init_driver.close_calls == 1

    lineage_driver = MockDriver()
    monkeypatch.setattr(graph_cli, "get_driver", lambda _settings: lineage_driver)
    monkeypatch.setattr(graph_cli, "init_schema", lambda _session: [])
    assert (
        graph_cli._do_graph_lineage(Settings(_env_file=None), quote_id="Q-missing") == EXIT_FAILURE
    )
    assert "not found in graph" in capsys.readouterr().err
    assert lineage_driver.close_calls == 1


@pytest.mark.parametrize(
    ("command", "kwargs"),
    [
        ("_do_graph_init", {}),
        ("_do_graph_status", {}),
        ("_do_graph_lineage", {"quote_id": "Q-1"}),
        ("_do_graph_speaker", {"speaker": "S1"}),
        ("_do_graph_divergence", {}),
        ("_do_graph_context", {}),
    ],
)
def test_graph_commands_close_driver_on_query_failure(
    command: str,
    kwargs: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    driver = MockDriver(RaisingSession())  # type: ignore[arg-type]
    monkeypatch.setattr(graph_cli, "get_driver", lambda _settings: driver)

    code = getattr(graph_cli, command)(Settings(_env_file=None), **kwargs)

    assert code == EXIT_FAILURE
    assert "cypher boom" in capsys.readouterr().err
    assert driver.close_calls == 1


def test_check_graph_connectivity_reports_verification_result() -> None:
    reachable = MockDriver()
    unreachable = MockDriver()
    unreachable.connected = False
    assert check_graph_connectivity(reachable) is True
    assert check_graph_connectivity(unreachable) is False


def test_graph_session_closes_session_on_exception() -> None:
    session = MagicMock()
    driver = MagicMock()
    driver.session.return_value = session

    with pytest.raises(ValueError, match="boom"), graph_session(driver, "neo4j") as active:
        assert active is session
        raise ValueError("boom")

    driver.session.assert_called_once_with(database="neo4j")
    session.close.assert_called_once_with()


def test_get_driver_raises_when_neo4j_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "neo4j", None)
    with pytest.raises(RuntimeError, match="neo4j"):
        graph_cli.get_driver(Settings(_env_file=None))


def test_resource_handle_reopens_after_close(monkeypatch: pytest.MonkeyPatch) -> None:
    drivers = [MockDriver(), MockDriver()]
    monkeypatch.setattr("media_quotes.graph.client.get_driver", lambda _settings: drivers.pop(0))
    handle = Neo4jResourceHandle(Settings(_env_file=None))

    first = handle.get_driver()
    handle.close()
    second = handle.get_driver()

    assert first is not second
    assert first.close_calls == 1
    handle.close()
    assert second.close_calls == 1
