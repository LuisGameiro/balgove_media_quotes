"""D3 — deterministic section curation, cross-outlet dedup and review badges.

Pure functions over validated export records. Selection never changes quote text or metadata;
it only chooses which records reach the digest and adds a separate status line for provisional
holds (24h `[Under Review]`), so the verbatim text stays intact.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from .enums import SECTION_IDS
from .models import ExportQuoteRecord
from .risk_controls.models import ObsolescenceFlag
from .text_utils import normalise_text


def allowed_sections() -> tuple[str, ...]:
    return tuple(sorted(SECTION_IDS))


def assign_section(record: ExportQuoteRecord) -> str:
    """Return the record's section if allowed, otherwise the canonical 'other' bucket."""
    return record.section_id if record.section_id in SECTION_IDS else "other"


def group_by_section(
    records: Sequence[ExportQuoteRecord],
) -> dict[str, list[ExportQuoteRecord]]:
    groups: dict[str, list[ExportQuoteRecord]] = {name: [] for name in allowed_sections()}
    for record in records:
        groups[assign_section(record)].append(record)
    for group in groups.values():
        group.sort(key=lambda item: (item.publication_date, item.quote_id))
    return groups


def dedupe_quotes(
    records: Sequence[ExportQuoteRecord], *, window_days: int = 14
) -> list[ExportQuoteRecord]:
    """Keep one copy of each normalised quote per rolling window.

    Preference: first-hand over relayed copy, non-claim over claim, then earliest publication
    and stable quote id. This cites the earliest first-hand outlet without altering text.
    """
    if window_days <= 0:
        raise ValueError("window_days must be positive")
    groups: dict[tuple[str, int], list[ExportQuoteRecord]] = {}
    for record in records:
        day_bucket = record.publication_date.toordinal() // window_days
        bucket = (normalise_text(record.quote_text_original), day_bucket)
        groups.setdefault(bucket, []).append(record)
    chosen: list[ExportQuoteRecord] = []
    for group in groups.values():
        group.sort(
            key=lambda item: (
                item.original_reporting_flag != "first-hand",
                item.claim_only,
                item.publication_date,
                item.quote_id,
            )
        )
        chosen.append(group[0])
    chosen.sort(key=lambda item: (item.publication_date, item.quote_id))
    return chosen


def review_badge(flag: ObsolescenceFlag, *, now: datetime, hold_hours: int = 24) -> str:
    """Return '[Under Review]' for a provisional obsolescence flag inside the hold window."""
    if flag.state not in {"pending", "needs_evidence", "escalated"}:
        return ""
    reviewed = flag.reviewed_at
    if reviewed is None:
        return "[Under Review]"
    if not isinstance(reviewed, datetime):
        raise TypeError("reviewed_at must be a datetime instance")
    if now - reviewed <= timedelta(hours=hold_hours):
        return "[Under Review]"
    return ""
