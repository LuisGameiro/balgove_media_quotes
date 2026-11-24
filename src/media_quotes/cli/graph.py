"""CLI subcommands for managing the derived Neo4j graph projection.

All graph operations are derived read projections; Postgres remains the authoritative
system of record.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from typing import Any

from ..config import Settings
from ..context_pack import build_graph_context_pack
from ..db.engine import create_db_engine
from ..enums import AcquisitionProvenance
from ..export import local_store_path, read_jsonl
from ..graph import (
    check_graph_connectivity,
    fetch_cross_tier_divergence,
    fetch_graphrag_subgraph,
    fetch_speaker_timeline,
    fetch_supersession_lineage,
    get_driver,
    graph_session,
    init_schema,
    project_records_to_graph,
    sync_postgres_to_graph,
)
from .common import EXIT_FAILURE, EXIT_OK


def _json_dump(payload: Any) -> str:
    return json.dumps(payload, indent=2, default=str)


def _do_graph_init(settings: Settings) -> int:
    """Apply graph constraints and indexes."""
    driver = None
    try:
        driver = get_driver(settings)
        with graph_session(driver, settings.neo4j_database) as session:
            applied = init_schema(session)
        print(_json_dump({"status": "ok", "applied_statements": len(applied)}))
        return EXIT_OK
    except Exception as exc:
        print(f"error initializing graph schema: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        if driver is not None:
            driver.close()


def _do_graph_status(settings: Settings) -> int:
    """Check connectivity and count nodes/edges in the graph."""
    driver = None
    try:
        driver = get_driver(settings)
        if not check_graph_connectivity(driver):
            print(_json_dump({"status": "unreachable", "uri": settings.neo4j_uri}))
            return EXIT_FAILURE

        with graph_session(driver, settings.neo4j_database) as session:
            node_rows = session.run(
                "MATCH (n) WHERE NOT n:ProjectionMetadata RETURN count(n) AS node_count"
            ).data()
            edge_rows = session.run("MATCH ()-[r]->() RETURN count(r) AS rel_count").data()
            node_count = node_rows[0]["node_count"] if node_rows else 0
            rel_count = edge_rows[0]["rel_count"] if edge_rows else 0

        print(
            _json_dump(
                {
                    "status": "connected",
                    "uri": settings.neo4j_uri,
                    "database": settings.neo4j_database,
                    "node_count": node_count,
                    "relationship_count": rel_count,
                }
            )
        )
        return EXIT_OK
    except Exception as exc:
        print(f"error checking graph status: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        if driver is not None:
            driver.close()


def _do_graph_sync(
    settings: Settings,
    *,
    from_db: bool = False,
    report_date: date | None = None,
    topic_id: str | None = None,
    provenance: AcquisitionProvenance = "fixture",
) -> int:
    """Sync quotes from Postgres or JSONL exports into Neo4j."""
    topic = topic_id or settings.active_topic
    driver = None
    engine = None
    try:
        driver = get_driver(settings)
        with graph_session(driver, settings.neo4j_database) as session:
            init_schema(session)
            if from_db:
                engine = create_db_engine(settings)
                stats = sync_postgres_to_graph(
                    engine, session, topic_id=topic, report_date=report_date
                )
            else:
                export_file = local_store_path(
                    settings.export_dir,
                    report_date or settings.report_date,
                    topic_id=topic,
                    provenance=provenance,
                )
                if not export_file.is_file():
                    print(
                        f"error: export file not found at {export_file} "
                        "(run mq export or use --from-db)",
                        file=sys.stderr,
                    )
                    return EXIT_FAILURE
                records = read_jsonl(export_file)
                stats = project_records_to_graph(records, session)

        print(
            _json_dump(
                {
                    "status": "ok",
                    "topic_id": topic,
                    "report_date": report_date.isoformat() if report_date else None,
                    "quotes_projected": stats.quotes_projected,
                    "sources_projected": stats.sources_projected,
                    "articles_projected": stats.articles_projected,
                    "speakers_projected": stats.speakers_projected,
                    "errors": stats.errors,
                }
            )
        )
        return EXIT_OK if not stats.errors else EXIT_FAILURE
    except Exception as exc:
        print(f"error syncing to graph: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        if driver is not None:
            driver.close()
        if engine is not None:
            engine.dispose()


def _do_graph_lineage(settings: Settings, *, quote_id: str) -> int:
    """Retrieve supersession lineage chain for a quote."""
    driver = None
    try:
        driver = get_driver(settings)
        with graph_session(driver, settings.neo4j_database) as session:
            lineage = fetch_supersession_lineage(session, quote_id)
        if not lineage:
            print(f"quote {quote_id} not found in graph", file=sys.stderr)
            return EXIT_FAILURE
        print(_json_dump(lineage))
        return EXIT_OK
    except Exception as exc:
        print(f"error fetching lineage: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        if driver is not None:
            driver.close()


def _do_graph_speaker(settings: Settings, *, speaker: str, topic_id: str | None = None) -> int:
    """Retrieve chronological statements by a speaker."""
    topic = topic_id or settings.active_topic
    driver = None
    try:
        driver = get_driver(settings)
        with graph_session(driver, settings.neo4j_database) as session:
            timeline = fetch_speaker_timeline(session, speaker, topic_id=topic)
        print(_json_dump({"speaker": speaker, "topic_id": topic, "quotes": timeline}))
        return EXIT_OK
    except Exception as exc:
        print(f"error fetching speaker timeline: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        if driver is not None:
            driver.close()


def _do_graph_divergence(
    settings: Settings, *, topic_id: str | None = None, section_id: str | None = None
) -> int:
    """Retrieve cross-tier and regional distribution breakdown."""
    topic = topic_id or settings.active_topic
    driver = None
    try:
        driver = get_driver(settings)
        with graph_session(driver, settings.neo4j_database) as session:
            divergence = fetch_cross_tier_divergence(session, topic_id=topic, section_id=section_id)
        print(_json_dump({"topic_id": topic, "section_id": section_id, "distribution": divergence}))
        return EXIT_OK
    except Exception as exc:
        print(f"error fetching divergence: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        if driver is not None:
            driver.close()


def _do_graph_context(
    settings: Settings,
    *,
    topic_id: str | None = None,
    section_id: str | None = None,
    max_tokens: int = 4000,
    limit: int = 25,
) -> int:
    """Generate a bounded GraphRAG context pack enriched with graph metadata."""
    topic = topic_id or settings.active_topic
    driver = None
    try:
        driver = get_driver(settings)
        with graph_session(driver, settings.neo4j_database) as session:
            subgraph = fetch_graphrag_subgraph(
                session, topic_id=topic, section_id=section_id, limit=limit
            )
        pack = build_graph_context_pack(subgraph, max_tokens=max_tokens, topic_id=topic)
        print(_json_dump(pack))
        return EXIT_OK
    except Exception as exc:
        print(f"error generating graph context pack: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    finally:
        if driver is not None:
            driver.close()
