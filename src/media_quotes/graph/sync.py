"""Deterministic projection of verified media quotes from Postgres / JSONL into Neo4j.

Postgres remains the authoritative single source of record. All graph writes are
idempotent Cypher MERGE projections.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Engine, text

from ..models import ExportQuoteRecord
from .protocol import GraphSessionProtocol, GraphSyncStats
from .queries import _normalize_speaker_key


def _get_val(obj: Any, key: str, default: Any = None) -> Any:
    val = obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)
    return default if val is None else val


_MERGE_QUOTE_CYPHER = """
MERGE (s:Source {source_id: $source_id})
SET s.source_name = $source_name,
    s.tier = $source_tier,
    s.country_region = $source_country_region,
    s.source_type = $source_type,
    s.claim_only = $claim_only,
    s.rights_status = $rights_status

MERGE (a:Article {canonical_url: $article_url})
SET a.title = $article_name,
    a.publication_date = $publication_date,
    a.content_hash = $content_hash,
    a.author = $article_author

MERGE (s)-[:PUBLISHED]->(a)

MERGE (q:Quote {canonical_quote_id: $quote_id})
SET q.quote_text_raw = $quote_text_raw,
    q.quote_text_en = $quote_text_en,
    q.quote_language = $quote_language,
    q.publication_date = $publication_date,
    q.is_obsolete = $is_obsolete,
    q.obsolete_reason = $obsolete_reason,
    q.topic_id = $topic_id

MERGE (a)-[:CONTAINS]->(q)

MERGE (sec:Section {section_id: $section_id})
MERGE (q)-[:IN_SECTION]->(sec)

MERGE (t:Topic {topic_id: $topic_id})
MERGE (q)-[:ON_TOPIC]->(t)
"""

_MERGE_SPEAKER_CYPHER = """
MATCH (q:Quote {canonical_quote_id: $quote_id})
MERGE (sp:Speaker {speaker_key: $speaker_key})
ON CREATE SET sp.display_name = $speaker_name
MERGE (sp)-[:SAID]->(q)
"""

_MERGE_TERM_CYPHER = """
MATCH (q:Quote {canonical_quote_id: $quote_id})
MERGE (tm:Term {term_key: $term_key})
MERGE (q)-[:MENTIONS]->(tm)
"""

_MERGE_SUPERSESSION_CYPHER = """
MATCH (newer:Quote {canonical_quote_id: $newer_quote_id})
MATCH (older:Quote {canonical_quote_id: $older_quote_id})
MERGE (newer)-[sup:SUPERSEDES]->(older)
SET sup.reason = $reason
"""

_WRITE_SYNC_METADATA_CYPHER = """
MERGE (m:ProjectionMetadata {key: 'media_quotes'})
SET m.last_synced_at = $last_synced_at,
    m.quotes_projected = $quotes_projected,
    m.sources_projected = $sources_projected,
    m.articles_projected = $articles_projected,
    m.speakers_projected = $speakers_projected,
    m.error_count = $error_count
"""


def project_records_to_graph(
    records: Sequence[ExportQuoteRecord | Mapping[str, Any]],
    session: GraphSessionProtocol,
) -> GraphSyncStats:
    """Project a sequence of verified quote records into Neo4j using idempotent MERGE."""
    quotes_count = 0
    sources_seen: set[str] = set()
    articles_seen: set[str] = set()
    speakers_seen: set[str] = set()
    errors: list[str] = []

    for rec in records:
        try:
            quote_id = str(_get_val(rec, "quote_id") or "")
            if not quote_id:
                continue

            source_id = str(_get_val(rec, "source_id") or "")
            article_url = str(_get_val(rec, "article_url") or "")
            pub_date = _get_val(rec, "publication_date")
            pub_date_str = (
                pub_date.isoformat() if hasattr(pub_date, "isoformat") else str(pub_date or "")
            )

            raw_source_type = _get_val(rec, "source_type", "independent")
            source_type_str = (
                raw_source_type.value if hasattr(raw_source_type, "value") else str(raw_source_type)
            )

            raw_rights = _get_val(rec, "rights_status", "license_held")
            rights_str = raw_rights.value if hasattr(raw_rights, "value") else str(raw_rights)

            quote_text_raw = str(
                _get_val(rec, "quote_text_original") or _get_val(rec, "quote_text_raw") or ""
            )

            quote_params: dict[str, Any] = {
                "source_id": source_id,
                "source_name": str(_get_val(rec, "source_name", source_id)),
                "source_tier": int(_get_val(rec, "source_tier", 1)),
                "source_country_region": str(
                    _get_val(rec, "source_country_region", "international")
                ),
                "source_type": source_type_str,
                "claim_only": bool(_get_val(rec, "claim_only", False)),
                "rights_status": rights_str,
                "article_url": article_url,
                "article_name": str(_get_val(rec, "article_name", "")),
                "article_author": str(_get_val(rec, "article_author", "")),
                "publication_date": pub_date_str,
                "content_hash": str(_get_val(rec, "content_hash", "")),
                "quote_id": quote_id,
                "quote_text_raw": quote_text_raw,
                "quote_text_en": str(_get_val(rec, "quote_text_en", "")),
                "quote_language": str(_get_val(rec, "quote_language", "")),
                "is_obsolete": bool(_get_val(rec, "is_obsolete", False)),
                "obsolete_reason": str(_get_val(rec, "obsolete_reason", "")),
                "topic_id": str(_get_val(rec, "topic_id", "ukraine-war")),
                "section_id": str(_get_val(rec, "section_id", "other")),
            }

            session.run(_MERGE_QUOTE_CYPHER, quote_params)
            quotes_count += 1
            sources_seen.add(source_id)
            articles_seen.add(article_url)

            # Speaker projection
            speaker = str(_get_val(rec, "speaker") or "").strip()
            if not speaker:
                speaker = str(_get_val(rec, "article_author") or "").strip()
            if speaker:
                speaker_key = _normalize_speaker_key(speaker)
                if speaker_key:
                    session.run(
                        _MERGE_SPEAKER_CYPHER,
                        {
                            "quote_id": quote_id,
                            "speaker_key": speaker_key,
                            "speaker_name": speaker,
                        },
                    )
                    speakers_seen.add(speaker_key)

            # Terms projection
            matched_terms = _get_val(rec, "matched_terms") or []
            for term in matched_terms:
                term_clean = re.sub(r"[^\w\s-]", "", str(term).strip().lower())
                term_key = re.sub(r"[-\s]+", "_", term_clean)
                if term_key:
                    session.run(
                        _MERGE_TERM_CYPHER,
                        {"quote_id": quote_id, "term_key": term_key},
                    )

            # Supersession projection. `superseded_by` carries the *newer* quotes that
            # replaced this one, so those edges run newer -> older. The notes-regex path
            # is the fallback for records without that list (this quote supersedes an
            # older one), so it runs in the opposite direction.
            superseded_ids = _get_val(rec, "superseded_by")
            newer_ids = (
                [str(item) for item in superseded_ids if item]
                if isinstance(superseded_ids, list)
                else []
            )
            for newer_id in newer_ids:
                session.run(
                    _MERGE_SUPERSESSION_CYPHER,
                    {
                        "newer_quote_id": newer_id,
                        "older_quote_id": quote_id,
                        "reason": str(_get_val(rec, "obsolete_reason", "superseded")),
                    },
                )
            superseded_id = _get_val(rec, "superseded_id")
            if not superseded_id and not newer_ids:
                notes_text = str(_get_val(rec, "notes", ""))
                m = re.search(r"supersedes:?\s*(Q-\d{8}-\d{4})", notes_text, re.IGNORECASE)
                if m:
                    superseded_id = m.group(1)
            if superseded_id:
                session.run(
                    _MERGE_SUPERSESSION_CYPHER,
                    {
                        "newer_quote_id": quote_id,
                        "older_quote_id": str(superseded_id),
                        "reason": str(_get_val(rec, "obsolete_reason", "superseded")),
                    },
                )
        except Exception as exc:
            errors.append(f"Error projecting quote {getattr(rec, 'quote_id', '?')}: {exc}")

    stats = GraphSyncStats(
        quotes_projected=quotes_count,
        sources_projected=len(sources_seen),
        articles_projected=len(articles_seen),
        speakers_projected=len(speakers_seen),
        errors=errors,
    )
    session.run(
        _WRITE_SYNC_METADATA_CYPHER,
        {
            "last_synced_at": datetime.now(UTC).isoformat(),
            "quotes_projected": stats.quotes_projected,
            "sources_projected": stats.sources_projected,
            "articles_projected": stats.articles_projected,
            "speakers_projected": stats.speakers_projected,
            "error_count": len(stats.errors),
        },
    )
    return stats


def sync_postgres_to_graph(
    engine: Engine,
    session: GraphSessionProtocol,
    topic_id: str = "ukraine-war",
    report_date: date | None = None,
) -> GraphSyncStats:
    """Read verified quotes from Postgres and project them to Neo4j."""
    query = """
    SELECT q.canonical_quote_id AS quote_id,
           q.quote_text_raw,
           q.quote_text_en,
           q.quote_language,
           q.speaker,
           q.matched_terms,
           q.topic_id,
           qc.section AS section_id,
           coalesce(obs.is_obsolete, false) AS is_obsolete,
           obs.reason AS obsolete_reason,
           -- obsolescence_flags stores internal quote_id UUIDs; the graph is keyed by
           -- canonical_quote_id, so resolve them before projecting SUPERSEDES edges.
           (SELECT array_agg(cq.canonical_quote_id ORDER BY cq.canonical_quote_id)
            FROM quotes cq
            WHERE cq.quote_id = ANY(obs.superseded_by)) AS superseded_by,
           q.quote_metadata->>'notes' AS notes,
           a.canonical_url AS article_url,
           a.title AS article_name,
           a.publication_date,
           a.content_hash,
           coalesce(a.article_metadata->>'byline', '') AS article_author,
           s.source_id,
           s.source_name,
           s.tier AS source_tier,
           s.country_region AS source_country_region,
           s.source_type,
           s.claim_only,
           s.rights_status
    FROM quotes q
    JOIN articles a ON q.article_id = a.article_id
    JOIN sources s ON a.source_id = s.source_id
    LEFT JOIN LATERAL (
        SELECT section
        FROM quote_classifications
        WHERE quote_id = q.quote_id
        ORDER BY created_at DESC
        LIMIT 1
    ) qc ON true
    LEFT JOIN LATERAL (
        SELECT is_obsolete, reason, superseded_by
        FROM obsolescence_flags
        WHERE quote_id = q.quote_id
        ORDER BY created_at DESC
        LIMIT 1
    ) obs ON true
    WHERE q.verification_status = 'verified'
      AND q.topic_id = :topic_id
      AND (:report_date IS NULL OR a.publication_date = :report_date)
    """
    params: dict[str, Any] = {
        "topic_id": topic_id,
        "report_date": report_date,
    }
    records: list[dict[str, Any]] = []
    with engine.connect() as conn:
        result = conn.execute(text(query), params)
        for row in result.mappings():
            records.append(dict(row))

    return project_records_to_graph(records, session)
