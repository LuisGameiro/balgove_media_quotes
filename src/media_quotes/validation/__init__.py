"""Data validation package with a flat compatibility API."""

from __future__ import annotations

import json
from pathlib import Path

from .common import check_columns, read_csv
from .quotes import check_quotes
from .sections import SECTION_COLUMNS, check_sections
from .sources import SOURCE_COLUMNS, check_sources

__all__ = [
    "SECTION_COLUMNS",
    "SOURCE_COLUMNS",
    "check_columns",
    "check_quotes",
    "check_sections",
    "check_sources",
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
    check_quotes(data_dir / "quotes.jsonl", by_id, section_ids, schema, errors)
    return errors
