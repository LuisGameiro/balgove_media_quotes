"""Opt-in smoke test for the real Neo4j driver and Cypher projection."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

import pytest

from media_quotes.config import Settings
from media_quotes.graph import get_driver, graph_session, init_schema, project_records_to_graph
from media_quotes.models import ExportQuoteRecord, VerificationRecord

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_NEO4J_INTEGRATION") != "1",
        reason="set RUN_NEO4J_INTEGRATION=1 with the Neo4j graph profile running",
    ),
]


def test_real_neo4j_projection_is_idempotent() -> None:
    settings = Settings(_env_file=None, sync_to_neo4j=True)
    quote_id = "Q-20990101-9999"
    source_id = "integration-test"
    article_url = "https://example.invalid/neo4j-integration"
    speaker_key = "integration_test_speaker"
    term_key = "integration_term"
    record = ExportQuoteRecord(
        quote_id=quote_id,
        quote_text_original="Integration projection statement.",
        quote_text_en="Integration projection statement.",
        source_id=source_id,
        source_name="Integration Test Source",
        source_tier=4,
        source_country_region="Test",
        source_type="state-controlled",
        rights_status="license_held",
        access_mode="public_rss_permitted",
        discovery_method="rss",
        claim_only=True,
        original_reporting_flag="unknown",
        source_language="en",
        publication_date=date(2099, 1, 1),
        article_name="Integration article",
        article_author="Integration Test Speaker",
        article_url=article_url,
        extraction_date=datetime(2099, 1, 1, tzinfo=UTC),
        section_id="other",
        is_obsolete=False,
        content_hash="b" * 64,
        verification=VerificationRecord(
            verbatim_match=True,
            metadata_verified=True,
            method="sha256-exact",
            verified_at=datetime(2099, 1, 1, tzinfo=UTC),
        ),
        matched_terms=["integration term"],
    )
    driver = get_driver(settings)
    try:
        with graph_session(driver, settings.neo4j_database) as session:
            init_schema(session)
            first = project_records_to_graph([record], session)
            second = project_records_to_graph([record], session)
            rows = session.run(
                "MATCH (q:Quote {canonical_quote_id: $quote_id}) RETURN count(q) AS quote_count",
                {"quote_id": quote_id},
            ).data()
        assert first.errors == second.errors == []
        assert first.quotes_projected == second.quotes_projected == 1
        assert rows[0]["quote_count"] == 1
    finally:
        with graph_session(driver, settings.neo4j_database) as session:
            session.run(
                "MATCH (q:Quote {canonical_quote_id: $id}) DETACH DELETE q", {"id": quote_id}
            )
            session.run(
                "MATCH (a:Article {canonical_url: $id}) DETACH DELETE a", {"id": article_url}
            )
            session.run("MATCH (s:Source {source_id: $id}) DETACH DELETE s", {"id": source_id})
            session.run(
                "MATCH (sp:Speaker {speaker_key: $id}) DETACH DELETE sp", {"id": speaker_key}
            )
            session.run("MATCH (tm:Term {term_key: $id}) DETACH DELETE tm", {"id": term_key})
        driver.close()
