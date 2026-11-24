"""Derived Neo4j graph projection package.

Postgres remains the authoritative system of record. This package provides
deterministic, idempotent projections, schema constraints, and analytical queries.
"""

from __future__ import annotations

from .client import check_graph_connectivity, get_driver, graph_session
from .protocol import (
    GraphDriverProtocol,
    GraphRecordProtocol,
    GraphResultProtocol,
    GraphSessionProtocol,
    GraphSyncStats,
)
from .queries import (
    fetch_cross_tier_divergence,
    fetch_graphrag_subgraph,
    fetch_speaker_timeline,
    fetch_supersession_lineage,
    fetch_sync_metadata,
)
from .schema import CONSTRAINTS, INDEXES, init_schema
from .sync import project_records_to_graph, sync_postgres_to_graph

__all__ = [
    "CONSTRAINTS",
    "INDEXES",
    "GraphDriverProtocol",
    "GraphRecordProtocol",
    "GraphResultProtocol",
    "GraphSessionProtocol",
    "GraphSyncStats",
    "check_graph_connectivity",
    "fetch_cross_tier_divergence",
    "fetch_graphrag_subgraph",
    "fetch_speaker_timeline",
    "fetch_supersession_lineage",
    "fetch_sync_metadata",
    "get_driver",
    "graph_session",
    "init_schema",
    "project_records_to_graph",
    "sync_postgres_to_graph",
]
