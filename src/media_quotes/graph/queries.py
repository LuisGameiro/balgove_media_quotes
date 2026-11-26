"""High-signal analytical Cypher queries for the derived Neo4j projection.

Covers:
1. Multi-hop supersession/obsolescence lineage chains.
2. Speaker position & stance timelines.
3. Cross-tier and regional narrative divergence.
4. GraphRAG context pack subgraph retrieval.
"""

from __future__ import annotations

import re
from typing import Any

from .protocol import GraphSessionProtocol


def _normalize_speaker_key(name: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", name.strip().lower())
    return re.sub(r"[-\s]+", "_", cleaned)


def fetch_sync_metadata(session: GraphSessionProtocol) -> dict[str, Any]:
    """Read the most recent projection sync summary."""
    res = session.run(
        "MATCH (m:ProjectionMetadata {key: 'media_quotes'}) "
        "RETURN m.last_synced_at AS last_synced_at, "
        "m.quotes_projected AS quotes_projected, "
        "m.error_count AS error_count"
    )
    rows = res.data()
    return rows[0] if rows else {}


def fetch_supersession_lineage(session: GraphSessionProtocol, quote_id: str) -> dict[str, Any]:
    """Retrieve the quote's immediate superseded and superseding links."""
    query = """
    MATCH (q:Quote {canonical_quote_id: $quote_id})
    OPTIONAL MATCH (q)-[sup:SUPERSEDES]->(ancestor:Quote)
    OPTIONAL MATCH (descendant:Quote)-[dsup:SUPERSEDES]->(q)
    RETURN q.canonical_quote_id AS quote_id,
           q.quote_text_raw AS quote_text,
           q.publication_date AS publication_date,
           q.is_obsolete AS is_obsolete,
           ancestor.canonical_quote_id AS superseded_id,
           ancestor.quote_text_raw AS superseded_text,
           sup.reason AS supersession_reason,
           descendant.canonical_quote_id AS superseded_by_id,
           descendant.quote_text_raw AS superseded_by_text,
           dsup.reason AS superseding_reason
    """
    res = session.run(query, {"quote_id": quote_id})
    rows = res.data()
    if not rows:
        return {}
    return rows[0]


def fetch_speaker_timeline(
    session: GraphSessionProtocol,
    speaker: str,
    topic_id: str = "ukraine-war",
) -> list[dict[str, Any]]:
    """Retrieve chronological statements by a speaker across all outlets and tiers."""
    cleaned_speaker = speaker.strip()
    if not cleaned_speaker:
        return []
    speaker_key = _normalize_speaker_key(cleaned_speaker)
    query = """
    MATCH (sp:Speaker)-[:SAID]->(q:Quote)-[:ON_TOPIC]->(t:Topic {topic_id: $topic_id})
    WHERE sp.speaker_key = $speaker_key OR toLower(sp.display_name) CONTAINS toLower($speaker)
    MATCH (q)-[:IN_SECTION]->(sec:Section)
    MATCH (a:Article)-[:CONTAINS]->(q)
    MATCH (s:Source)-[:PUBLISHED]->(a)
    RETURN sp.display_name AS speaker,
           q.canonical_quote_id AS quote_id,
           q.quote_text_raw AS quote_text,
           q.publication_date AS publication_date,
           sec.section_id AS section_id,
           s.source_id AS source_id,
           s.tier AS source_tier,
           s.country_region AS country_region,
           q.is_obsolete AS is_obsolete
    ORDER BY CASE WHEN sp.speaker_key = $speaker_key THEN 0 ELSE 1 END ASC,
             q.publication_date ASC, q.canonical_quote_id ASC
    """
    res = session.run(
        query,
        {
            "speaker": cleaned_speaker,
            "speaker_key": speaker_key,
            "topic_id": topic_id,
        },
    )
    return res.data()


def fetch_cross_tier_divergence(
    session: GraphSessionProtocol,
    topic_id: str = "ukraine-war",
    section_id: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate quote volume and outlet distribution across source tiers and regions."""
    query = """
    MATCH (s:Source)-[:PUBLISHED]->(a:Article)
    MATCH (a)-[:CONTAINS]->(q:Quote)-[:ON_TOPIC]->(t:Topic {topic_id: $topic_id})
    MATCH (q)-[:IN_SECTION]->(sec:Section)
    WHERE ($section_id IS NULL OR sec.section_id = $section_id)
    RETURN s.tier AS tier,
           s.country_region AS country_region,
           s.claim_only AS claim_only,
           sec.section_id AS section_id,
           count(q) AS quote_count,
           collect(DISTINCT s.source_id) AS sources
    ORDER BY s.tier ASC, quote_count DESC
    """
    res = session.run(query, {"topic_id": topic_id, "section_id": section_id})
    return res.data()


def fetch_graphrag_subgraph(
    session: GraphSessionProtocol,
    topic_id: str = "ukraine-war",
    section_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Retrieve active quotes enriched with speaker and supersession context."""
    query = """
    MATCH (s:Source)-[:PUBLISHED]->(a:Article)
    MATCH (a)-[:CONTAINS]->(q:Quote)-[:ON_TOPIC]->(t:Topic {topic_id: $topic_id})
    MATCH (q)-[:IN_SECTION]->(sec:Section)
    WHERE q.is_obsolete = false
      AND ($section_id IS NULL OR sec.section_id = $section_id)
    WITH s, a, q, sec
    ORDER BY q.publication_date DESC, s.tier ASC
    LIMIT $limit
    OPTIONAL MATCH (sp:Speaker)-[:SAID]->(q)
    OPTIONAL MATCH (q)-[sup:SUPERSEDES]->(old_q:Quote)
    RETURN q.canonical_quote_id AS quote_id,
           q.quote_text_raw AS quote_text,
           q.quote_text_en AS quote_text_en,
           q.quote_language AS quote_language,
           q.publication_date AS publication_date,
           a.canonical_url AS article_url,
           s.source_id AS source_id,
           s.tier AS tier,
           s.country_region AS country_region,
           s.claim_only AS claim_only,
           sec.section_id AS section_id,
           sp.display_name AS speaker,
           old_q.canonical_quote_id AS supersedes_quote_id,
           sup.reason AS supersession_reason
    ORDER BY q.publication_date DESC, s.tier ASC
    """
    res = session.run(
        query,
        {
            "topic_id": topic_id,
            "section_id": section_id,
            "limit": limit,
        },
    )
    return res.data()
