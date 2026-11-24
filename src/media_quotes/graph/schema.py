"""Neo4j schema definitions: uniqueness constraints and performance indexes.

Postgres remains the single authoritative system of record; Neo4j is a derived,
rebuildable graph projection. All constraints ensure idempotent MERGE projections.
"""

from __future__ import annotations

from typing import Final

from .protocol import GraphSessionProtocol

CONSTRAINTS: Final[tuple[str, ...]] = (
    "CREATE CONSTRAINT source_id_unique IF NOT EXISTS "
    "FOR (s:Source) REQUIRE s.source_id IS UNIQUE;",
    "CREATE CONSTRAINT article_url_unique IF NOT EXISTS "
    "FOR (a:Article) REQUIRE a.canonical_url IS UNIQUE;",
    "CREATE CONSTRAINT quote_id_unique IF NOT EXISTS "
    "FOR (q:Quote) REQUIRE q.canonical_quote_id IS UNIQUE;",
    "CREATE CONSTRAINT speaker_key_unique IF NOT EXISTS "
    "FOR (sp:Speaker) REQUIRE sp.speaker_key IS UNIQUE;",
    "CREATE CONSTRAINT section_id_unique IF NOT EXISTS "
    "FOR (sec:Section) REQUIRE sec.section_id IS UNIQUE;",
    "CREATE CONSTRAINT topic_id_unique IF NOT EXISTS FOR (t:Topic) REQUIRE t.topic_id IS UNIQUE;",
    "CREATE CONSTRAINT term_key_unique IF NOT EXISTS FOR (tm:Term) REQUIRE tm.term_key IS UNIQUE;",
)

INDEXES: Final[tuple[str, ...]] = (
    "CREATE INDEX quote_obsolete_idx IF NOT EXISTS FOR (q:Quote) ON (q.is_obsolete);",
    "CREATE INDEX quote_date_idx IF NOT EXISTS FOR (q:Quote) ON (q.publication_date);",
    "CREATE INDEX source_tier_idx IF NOT EXISTS FOR (s:Source) ON (s.tier, s.country_region);",
    "CREATE INDEX source_claim_idx IF NOT EXISTS FOR (s:Source) ON (s.claim_only);",
)


def init_schema(session: GraphSessionProtocol) -> list[str]:
    """Execute all schema constraints and indexes against the graph session.

    Returns the list of executed statements.
    """
    applied: list[str] = []
    for stmt in CONSTRAINTS + INDEXES:
        session.run(stmt)
        applied.append(stmt)
    return applied
