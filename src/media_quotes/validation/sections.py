"""Section catalog validation."""

from __future__ import annotations

from ..enums import SECTION_IDS
from .common import check_columns

SECTION_COLUMNS = ["section_id", "section_name", "description"]


def check_sections(sections: list[dict[str, str]], errors: list[str]) -> set[str]:
    check_columns(sections, SECTION_COLUMNS, "sections.csv", errors)
    ids: set[str] = set()
    for line, row in enumerate(sections, start=2):
        where = f"sections.csv line {line}"
        if any(row.get(column) is None for column in SECTION_COLUMNS):
            # check_columns already reported the header problem; never index a missing key.
            continue
        sid = row["section_id"]
        if not sid:
            errors.append(f"{where}: missing section_id")
            continue
        if sid in ids:
            errors.append(f"{where}: duplicate section_id {sid}")
        ids.add(sid)
        if not row["section_name"] or not row["description"]:
            errors.append(f"{where}: section_name and description are required")
        if sid not in SECTION_IDS:
            errors.append(f"{where}: unknown section_id {sid!r}")
    return ids
