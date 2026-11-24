"""Protocols and dataclasses for graph database sessions and drivers.

Enables pure offline testing with deterministic mock sessions while adhering to
the Neo4j session/driver semantics.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class GraphSyncStats:
    """Summary of elements updated or projected during a graph sync."""

    quotes_projected: int = 0
    sources_projected: int = 0
    articles_projected: int = 0
    speakers_projected: int = 0
    relationships_created: int = 0
    errors: list[str] = field(default_factory=list)


class GraphRecordProtocol(Protocol):
    """Protocol representing a single Cypher query result row."""

    def data(self) -> dict[str, Any]:
        """Convert row to a standard python dictionary."""
        ...

    def get(self, key: str, default: Any = None) -> Any: ...


class GraphResultProtocol(Protocol):
    """Protocol representing the cursor/iterable returned by Cypher execution."""

    def __iter__(self) -> Any: ...

    def data(self) -> list[dict[str, Any]]: ...


class GraphSessionProtocol(Protocol):
    """Protocol for a Neo4j session."""

    def run(
        self, query: str, parameters: Mapping[str, Any] | None = None, **kwargs: Any
    ) -> GraphResultProtocol: ...

    def close(self) -> None: ...


class GraphDriverProtocol(Protocol):
    """Protocol for a Neo4j driver."""

    def session(self, **kwargs: Any) -> GraphSessionProtocol: ...

    def close(self) -> None: ...

    def verify_connectivity(self) -> None: ...
