"""C3 — two-stage, fail-closed quote verification (docs/12 Phase C).

Stage 1 runs BEFORE any LLM call and drops boilerplate, overlength and unverified spans so we
never spend tokens on junk. Stage 2 is the existing deterministic verifier (exact span,
attribution, metadata, first-hand evidence). Only `verified` results are publishable.
No network, no DB.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import QuoteCandidate, VerificationResult
from .registry import SourceRegistryRow
from .text_utils import exact_span_match, normalise_text
from .verification import verify_quote

STRICT = ConfigDict(strict=True, extra="forbid")


class PreFilterResult(BaseModel):
    """Stage 1 outcome with per-reason rejection counts for the audit report."""

    model_config = STRICT

    kept: list[QuoteCandidate]
    rejected: dict[str, int] = Field(default_factory=dict)


class PipelineResult(BaseModel):
    """Combined outcome; `publishable` is derived, never caller-supplied."""

    model_config = STRICT

    prefilter: PreFilterResult
    results: list[VerificationResult]
    publishable: list[QuoteCandidate] = Field(default_factory=list)


def pre_llm_filter(
    candidates: Sequence[QuoteCandidate],
    *,
    text: str,
    policy_flags: Mapping[str, Any],
    max_quote_words: int = 30,
    min_quote_words: int = 5,
) -> PreFilterResult:
    """Stage 1: cheap deterministic gates, no LLM call involved."""
    if max_quote_words <= 0 or min_quote_words <= 0:
        raise ValueError("word limits must be positive")
    kept: list[QuoteCandidate] = []
    rejected: dict[str, int] = {}
    seen: set[str] = set()

    def drop(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    for candidate in candidates:
        if not exact_span_match(
            text, candidate.quote_text, candidate.char_start, candidate.char_end
        ):
            drop("span_mismatch")
            continue
        words = candidate.quote_text.split()
        if len(words) < min_quote_words:
            drop("too_short")
            continue
        if len(words) > max_quote_words:
            drop("over_word_limit")
            continue
        norm = normalise_text(candidate.quote_text)
        if len(norm) < 20:
            drop("empty_after_normalisation")
            continue
        if norm in seen:
            drop("duplicate")
            continue
        if policy_flags.get("original_reporting_flag", "unknown") != "first-hand":
            drop("not_first_hand")
            continue
        seen.add(norm)
        kept.append(candidate)
    return PreFilterResult(kept=kept, rejected=rejected)


def verify_pipeline(
    candidates: Sequence[QuoteCandidate],
    *,
    text: str,
    source_row: SourceRegistryRow,
    policy_flags: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
    window_start: date,
    window_end: date,
    max_quote_words: int = 30,
    now: datetime | None = None,
) -> PipelineResult:
    """Stage 1 + Stage 2; publishable contains ONLY `verified` quotes."""
    stage1 = pre_llm_filter(
        candidates,
        text=text,
        policy_flags=policy_flags,
        max_quote_words=max_quote_words,
    )
    results = [
        verify_quote(
            candidate,
            text,
            source_row,
            policy_flags,
            metadata=metadata,
            window_start=window_start,
            window_end=window_end,
            now=now or datetime.now(UTC),
        )
        for candidate in stage1.kept
    ]
    publishable = [item.candidate for item in results if item.status == "verified"]
    return PipelineResult(prefilter=stage1, results=results, publishable=publishable)
