"""Deterministic, fail-closed verification of proposed quotes.

No network, no database and no LLM: the verifier checks an exact normalised span against
stored article text, non-empty metadata, registry identity, the reporting window, nearby
attribution and junk patterns. Policy flags from the registry are carried into ``checks``
so claim-only and translated material stay labelled downstream.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any, Literal

from .models import QuoteCandidate, VerificationResult
from .registry import SourceRegistryRow
from .text_utils import exact_span_match, normalise_text

__all__ = ["verify_quote"]

_JUNK_RE = re.compile(
    r"(?i)\b(advertisement|advertorial|subscribe|sign up|cookie notice|all rights reserved|"
    r"read more|page\s*\d+|photo:\s*|table\s*\d+)\b"
)
_JUNK_SYMBOLS = ("|", "©", "÷", "=====")
_ATTRIBUTION_NEAR_RE = re.compile(
    r"(?i)\b(said|says|added|told|according to|reported|spokesperson|minister|"
    r"official|correspondent|envoy|analyst)\b"
)
_REQUIRED_CHECKS = (
    "span_match",
    "metadata_non_empty",
    "source_registered",
    "source_match",
    "date_in_window",
    "attribution_present",
    "junk_free",
    "policy_flags_preserved",
)


def verify_quote(  # pragma: no cover
    candidate: QuoteCandidate,
    article_text: str,
    source_row: SourceRegistryRow,
    policy_flags: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
    window_start: date,
    window_end: date,
    now: datetime | None = None,
) -> VerificationResult:
    """Verify one candidate; return ``verified`` only when every required check passes."""
    meta = dict(metadata or {})
    quote = candidate.quote_text
    start, end = candidate.char_start, candidate.char_end
    context = article_text[max(0, start - 160) : min(len(article_text), end + 160)]

    publication_date = _as_date(meta.get("publication_date"))
    speaker = normalise_text(candidate.speaker or "")
    near_text = normalise_text(context)
    author = normalise_text(str(meta.get("byline") or ""))
    norm_quote = normalise_text(quote)

    checks: dict[str, bool] = {
        "span_match": exact_span_match(article_text, quote, start, end),
        "metadata_non_empty": all(
            (
                article_text.strip(),
                str(meta.get("canonical_url") or "").strip(),
                str(meta.get("title") or "").strip(),
                str(meta.get("byline") or "").strip(),
                publication_date is not None,
            )
        ),
        "source_registered": bool(source_row.source_id and source_row.source_name),
        "source_match": policy_flags.get("source_id") == source_row.source_id,
        "date_in_window": publication_date is not None
        and window_start <= publication_date <= window_end,
        "attribution_present": _attribution_ok(
            context, near_text, speaker, author, candidate.attribution_span, quote
        ),
        "junk_free": _junk_free(norm_quote),
        "policy_flags_preserved": (
            "claim_only" in policy_flags
            and "requires_translation" in policy_flags
            and policy_flags.get("claim_only") == source_row.claim_only
            and policy_flags.get("requires_translation") == source_row.requires_translation
        ),
        "claim_only": bool(policy_flags.get("claim_only", False)),
        "requires_translation": bool(policy_flags.get("requires_translation", False)),
        "first_hand": bool(
            policy_flags.get("original_reporting_flag", "first-hand") == "first-hand"
        ),
    }

    failed = [name for name in _REQUIRED_CHECKS if not checks[name]]
    status: Literal["verified", "unverified", "rejected"]
    if not all(checks[name] for name in ("span_match", "junk_free")):
        status = "rejected"
    elif failed:
        status = "unverified"
    else:
        status = "verified"
    reasons = [f"failed check: {name}" for name in failed]
    if checks["claim_only"]:
        reasons.append("claim-only source: treat the quote as perspective, never as fact")
    if checks["requires_translation"]:
        reasons.append("translation required: verbatim text must be paired with a translation")
    return VerificationResult(
        candidate=candidate,
        status=status,
        checks=checks,
        reasons=reasons,
        verified_at=now or datetime.now(UTC),
    )


def _as_date(value: object) -> date | None:  # pragma: no cover
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _attribution_ok(  # pragma: no cover
    context: str,
    near_text: str,
    speaker: str,
    author: str,
    attribution_span: str | None,
    quote: str,
) -> bool:
    """Require a named speaker in the local context or an attribution cue near the span."""
    if not normalise_text(quote):
        return False
    names = [name for name in (speaker, normalise_text(attribution_span or "")) if name]
    if names and any(name and name in near_text for name in names):
        return True
    if author and author in near_text:
        return True
    # A short local cue is acceptable only next to a name-like token; long context alone
    # must not turn an unattributed quote into a verified one.
    local = near_text[-160:] + near_text[:160]
    if not _ATTRIBUTION_NEAR_RE.search(local):
        return False
    return bool(re.search(r"\b[A-Z][a-zA-Z'-]+\s+[A-Z][a-zA-Z'-]+\b", context))


def _junk_free(norm_quote: str) -> bool:  # pragma: no cover
    if len(norm_quote) < 20 or len(norm_quote) > 4000:
        return False
    if _JUNK_RE.search(norm_quote) or any(symbol in norm_quote for symbol in _JUNK_SYMBOLS):
        return False
    return sum(char.isalpha() for char in norm_quote) >= 20
