"""Data validation package with a flat compatibility API."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ..source_configs import load_routes_from_sources, load_source_configs
from ..source_topics import validate_topic_configuration
from ..topics import load_all_topic_rules
from .common import check_columns, read_csv
from .modular import check_source_configs, check_topic_packs
from .quotes import check_quotes
from .sections import SECTION_COLUMNS, check_sections
from .sources import SOURCE_COLUMNS, check_sources

__all__ = [
    "SECTION_COLUMNS",
    "SOURCE_COLUMNS",
    "check_columns",
    "check_quotes",
    "check_sections",
    "check_source_configs",
    "check_sources",
    "check_topic_packs",
    "read_csv",
    "validate_data_dir",
]


def validate_data_dir(data_dir: Path) -> list[str]:
    errors: list[str] = []
    sources = read_csv(data_dir / "sources.csv")
    sections = read_csv(data_dir / "sections.csv")
    schema = json.loads((data_dir / "quotes.schema.json").read_text(encoding="utf-8"))
    by_id = check_sources(sources, errors)
    section_ids = check_sections(sections, errors)
    topic_packs = check_topic_packs(data_dir / "topics", errors)
    check_source_configs(data_dir / "sources", by_id, topic_packs, errors)
    try:
        configs = load_source_configs(data_dir / "sources")
        routes = load_routes_from_sources(
            data_dir / "sources", fallback_csv=data_dir / "source_topic_routes.csv"
        )
        rules = load_all_topic_rules(
            data_dir / "topics", fallback_rules_path=data_dir / "topic_rules.json"
        )
        if configs:
            rows_for_check: list[Any] = [config.to_registry_row() for config in configs]
        else:
            # Legacy CSV-only data dirs still need the route check.
            rows_for_check = [
                SimpleNamespace(source_id=row["source_id"], usable=row.get("enabled") == "true")
                for row in sources
            ]
        errors.extend(validate_topic_configuration(routes, rules, rows_for_check))
    except (OSError, ValueError) as exc:
        errors.append(f"sources/: cannot validate topic routes: {exc}")
    valid_sections = set(section_ids)
    for pack in topic_packs.values():
        valid_sections.update(pack.section_ids())
    check_quotes(data_dir / "quotes.jsonl", by_id, valid_sections, schema, errors)
    return errors
