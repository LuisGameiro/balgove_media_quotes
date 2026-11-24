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

from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from media_quotes import cli
from media_quotes.config import Settings
from media_quotes.context_pack import build_graph_context_pack
from media_quotes.dashboard import graph_projection_summary
from media_quotes.graph import (
    CONSTRAINTS,
    INDEXES,
    fetch_cross_tier_divergence,
    fetch_graphrag_subgraph,
    fetch_speaker_timeline,
    fetch_supersession_lineage,
    init_schema,
    project_records_to_graph,
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

    def session(self, **kwargs: Any) -> MockSession:
        return self._session

    def verify_connectivity(self) -> None:
        if not self.connected:
            raise ConnectionError("Neo4j unreachable")

    def close(self) -> None:
        pass


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
    assert any("MERGE (q)-[sup:SUPERSEDES]->(ancestor)" in q for q in queries_text)


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
            "count(n) AS node_count": [{"node_count": 10}],
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


def test_dashboard_graph_projection_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. When sync_to_neo4j is False
    disabled_settings = Settings(_env_file=None, sync_to_neo4j=False)
    res_disabled = graph_projection_summary(disabled_settings)
    assert res_disabled["status"] == "disabled"

    # 2. When sync_to_neo4j is True and connected
    mock_session = MockSession(
        {
            "count(n) AS node_count": [{"node_count": 42}],
            "count(r) AS rel_count": [{"rel_count": 84}],
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
