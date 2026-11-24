"""D3 — deterministic section curation, cross-outlet dedup and review badges.

Pure functions over validated export records. Selection never changes quote text or metadata;
it only chooses which records reach the digest and adds a separate status line for provisional
holds (24h `[Under Review]`), so the verbatim text stays intact.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from .enums import SECTION_IDS
from .models import ExportQuoteRecord
from .risk_controls.models import ObsolescenceFlag
from .text_utils import normalise_text

if TYPE_CHECKING:
    from .topics import TopicPack


def allowed_sections(*, topic_pack: TopicPack | None = None) -> tuple[str, ...]:
    if topic_pack is not None:
        return tuple(sorted(topic_pack.section_ids()))
    return tuple(sorted(SECTION_IDS))


def assign_section(
    record: ExportQuoteRecord,
    *,
    allowed: Collection[str] | None = None,
    default_section: str = "other",
) -> str:
    """Return the record's section if allowed, otherwise the canonical fallback bucket."""
    valid = allowed if allowed is not None else SECTION_IDS
    return record.section_id if record.section_id in valid else default_section


def group_by_section(
    records: Sequence[ExportQuoteRecord],
    *,
    topic_pack: TopicPack | None = None,
) -> dict[str, list[ExportQuoteRecord]]:
    section_names = allowed_sections(topic_pack=topic_pack)
    default_section = topic_pack.default_section if topic_pack is not None else "other"
    groups: dict[str, list[ExportQuoteRecord]] = {name: [] for name in section_names}
    for record in records:
        target = assign_section(record, allowed=section_names, default_section=default_section)
        groups.setdefault(target, []).append(record)
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
    groups: dict[str, list[ExportQuoteRecord]] = {}
    for record in records:
        groups.setdefault(normalise_text(record.quote_text_original).casefold(), []).append(record)
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
        kept_dates: list[date] = []
        for record in group:
            if any(abs((record.publication_date - d).days) < window_days for d in kept_dates):
                continue
            chosen.append(record)
            kept_dates.append(record.publication_date)
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
