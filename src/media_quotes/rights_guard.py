"""F1 — DEC-10 rights/limits guard (CHK-19).

Pure, fail-closed filter applied before rendering. Quotes outside the licensed word limit or
without a named attribution are withheld from the digest and reported as an audit gap. The
source text is never modified; the record is simply not published.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from .enums import APPROVED_RIGHTS
from .models import ExportQuoteRecord

STRICT = ConfigDict(strict=True, extra="forbid")


class RightsDecision(BaseModel):
    model_config = STRICT

    kept: list[ExportQuoteRecord]
    rejected: list[dict[str, str]]


def _word_count(text: str) -> int:
    words = text.split()
    if len(words) <= 1 and len(text) > 20:
        return len(text)
    return len(words)


def apply_rights_limits(
    records: Sequence[ExportQuoteRecord],
    *,
    max_quote_words: int,
    attribution_required: bool = True,
) -> RightsDecision:
    """Drop records that would breach the licence; never rewrite text to fit."""
    if max_quote_words <= 0:
        raise ValueError("max_quote_words must be positive")
    kept: list[ExportQuoteRecord] = []
    rejected: list[dict[str, str]] = []
    for record in records:
        reasons: list[str] = []
        if record.rights_status not in APPROVED_RIGHTS:
            reasons.append(f"rights_blocked:{record.rights_status}")
        words = max(_word_count(record.quote_text_original), _word_count(record.quote_text_en))
        if words > max_quote_words:
            reasons.append(f"over_word_limit:{words}>{max_quote_words}")
        if attribution_required and not record.article_author.strip():
            reasons.append("missing_attribution")
        if reasons:
            rejected.append({"quote_id": record.quote_id, "reason": ";".join(reasons)})
            continue
        kept.append(record)
    return RightsDecision(kept=kept, rejected=rejected)
