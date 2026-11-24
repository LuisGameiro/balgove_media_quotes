"""Connection management for Neo4j graph driver and sessions."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from ..config import Settings
from .protocol import GraphDriverProtocol, GraphSessionProtocol


def get_driver(settings: Settings) -> GraphDriverProtocol:
    """Instantiate a Neo4j driver using the configured settings.

    Raises RuntimeError if the 'neo4j' package is not installed.
    """
    try:
        from neo4j import GraphDatabase
    except ImportError as err:
        raise RuntimeError(
            "The 'neo4j' library is required for graph projection features. "
            "Install with 'uv pip install neo4j' or 'pip install .[graph]'."
        ) from err

    driver = GraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password_value),
    )
    return cast(GraphDriverProtocol, driver)


def check_graph_connectivity(driver: GraphDriverProtocol) -> bool:
    """Verify that the driver can connect to the Neo4j cluster/instance."""
    try:
        driver.verify_connectivity()
        return True
    except Exception:
        return False


@contextmanager
def graph_session(
    driver: GraphDriverProtocol, database: str = "neo4j"
) -> Iterator[GraphSessionProtocol]:
    """Context manager yielding a GraphSessionProtocol."""
    session = driver.session(database=database)
    try:
        yield session
    finally:
        session.close()
