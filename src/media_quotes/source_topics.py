"""Approved discovery routes per source, loaded from a strict.csv contract.

A route is a rights-and-discovery decision, not merely a URL. Duplicated
``source_id`` + ``route_id`` pairs are rejected so a fallback can never resolve
to two different routes.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_EXPECTED_COLUMNS: tuple[str, ...] = (
    "source_id",
    "route_id",
    "route_type",
    "discovery_url",
    "topic_rule_id",
    "priority",
    "fallback_route_id",
    "approved",
    "license_reference",
    "translation_rights_confirmed",
    "notes",
)
_REQUIRED_COLUMNS = frozenset({"source_id", "route_id", "route_type", "topic_rule_id"})


@dataclass(frozen=True, slots=True)
class SourceTopicRoute:
    source_id: str
    route_id: str
    route_type: str
    discovery_url: str
    topic_rule_id: str
    priority: int
    fallback_route_id: str
    approved: bool
    license_reference: str
    translation_rights_confirmed: bool
    notes: str


def _parse_bool(value: str, *, field: str, line: int) -> bool:
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    raise ValueError(f"line {line}: {field} must be 'true' or 'false', got {value!r}")


def _parse_int(value: str, *, field: str, line: int) -> int:
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"line {line}: {field} must be an integer, got {value!r}") from exc


def load_source_topic_routes(path: Path) -> dict[str, list[SourceTopicRoute]]:
    """Load every approved-or-not route, keyed by source and sorted by priority."""
    routes: dict[str, list[SourceTopicRoute]] = {}
    keys: set[tuple[str, str]] = set()
    with Path(path).open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        if fieldnames is None:
            raise ValueError("source topic routes CSV has no header row")
        if len(fieldnames) != len(set(fieldnames)):
            raise ValueError("source topic routes CSV has duplicate column names")
        missing = set(_EXPECTED_COLUMNS) - set(fieldnames)
        extra = set(fieldnames) - set(_EXPECTED_COLUMNS)
        if missing or extra:
            raise ValueError(
                f"source topic routes CSV columns are invalid: "
                f"missing={sorted(missing)}, unexpected={sorted(extra)}"
            )

        for line, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"line {line}: more values than header columns")
            values = {column: (row.get(column) or "").strip() for column in _EXPECTED_COLUMNS}
            empty = sorted(name for name in _REQUIRED_COLUMNS if not values[name])
            if empty:
                raise ValueError(f"line {line}: empty required columns {empty}")
            key = (values["source_id"], values["route_id"])
            if key in keys:
                raise ValueError(f"line {line}: duplicate source_id+route_id {key}")
            keys.add(key)
            route = SourceTopicRoute(
                source_id=values["source_id"],
                route_id=values["route_id"],
                route_type=values["route_type"],
                discovery_url=values["discovery_url"],
                topic_rule_id=values["topic_rule_id"],
                priority=_parse_int(values["priority"], field="priority", line=line),
                fallback_route_id=values["fallback_route_id"],
                approved=_parse_bool(values["approved"], field="approved", line=line),
                license_reference=values["license_reference"],
                translation_rights_confirmed=_parse_bool(
                    values["translation_rights_confirmed"],
                    field="translation_rights_confirmed",
                    line=line,
                ),
                notes=values["notes"],
            )
            routes.setdefault(route.source_id, []).append(route)

    for source_routes in routes.values():
        source_routes.sort(key=lambda route: (route.priority, route.route_id))
    return {source_id: routes[source_id] for source_id in sorted(routes)}


def validate_topic_configuration(
    routes: Mapping[str, list[SourceTopicRoute]],
    rules: Mapping[str, Mapping[str, Any]],
    rows: Iterable[Any],
) -> list[str]:
    """Check rule references, fallback chains and approved routes before live work."""
    errors: list[str] = []
    for row in rows:
        source_id = str(row.source_id)
        source_routes = routes.get(source_id, [])
        known_ids = {route.route_id for route in source_routes}
        route_map = {route.route_id: route for route in source_routes}
        if not source_routes:
            errors.append(f"{source_id}: no source topic routes")
        for route in source_routes:
            if route.topic_rule_id not in rules:
                errors.append(
                    f"{source_id}/{route.route_id}: unresolved topic rule {route.topic_rule_id!r}"
                )
            if route.fallback_route_id and route.fallback_route_id not in known_ids:
                errors.append(
                    f"{source_id}/{route.route_id}: unresolved fallback {route.fallback_route_id!r}"
                )
            elif route.fallback_route_id:
                visited = [route.route_id]
                curr: str | None = route.fallback_route_id
                while curr and curr in route_map:
                    if curr in visited:
                        chain = " -> ".join([*visited, curr])
                        errors.append(
                            f"{source_id}/{route.route_id}: cyclic fallback route chain ({chain})"
                        )
                        break
                    visited.append(curr)
                    curr = route_map[curr].fallback_route_id
            if route.approved and not route.license_reference:
                errors.append(
                    f"{source_id}/{route.route_id}: approved route has no licence reference"
                )
            if route.translation_rights_confirmed and not route.license_reference:
                errors.append(
                    f"{source_id}/{route.route_id}: translation rights confirmed "
                    "without licence reference"
                )
        if getattr(row, "usable", False) and not any(route.approved for route in source_routes):
            errors.append(f"{source_id}: enabled source has no approved route")
    return errors
