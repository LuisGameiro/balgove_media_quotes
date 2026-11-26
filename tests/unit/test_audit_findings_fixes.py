"""Regressions for the third-party audit findings fixed in this change."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from media_quotes.models import QuoteCandidate
from media_quotes.processing.verification import verify_quote
from media_quotes.registry import load_sources_csv
from media_quotes.text_utils import require_safe_id

ROW = load_sources_csv(Path("data/sources.csv"))[0]


def test_quote_cannot_self_satisfy_attribution() -> None:
    article = "European Union said negotiations would proceed after the meeting."
    quote = "European Union said negotiations would proceed"
    candidate = QuoteCandidate(
        quote_text=quote,
        article_id=uuid5(NAMESPACE_URL, "test-article"),
        char_start=0,
        char_end=len(quote),
        speaker="",
        attribution_span="",
    )
    result = verify_quote(
        candidate,
        article_text=article,
        source_row=ROW,
        policy_flags={
            "source_id": ROW.source_id,
            "claim_only": ROW.claim_only,
            "requires_translation": ROW.requires_translation,
            "original_reporting_flag": "first-hand",
        },
        metadata={
            "canonical_url": "https://example.invalid/a",
            "title": "Meeting",
            "byline": "Unrelated Author",
            "publication_date": "2026-11-19",
        },
        window_start=date(2026, 11, 1),
        window_end=date(2026, 11, 30),
    )
    assert result.checks["span_match"] is True
    assert result.checks["attribution_present"] is False
    assert result.status == "unverified"


def test_artifact_ids_reject_path_separators() -> None:
    with pytest.raises(ValueError, match="invalid run_id"):
        require_safe_id("../../target", "run_id")
    with pytest.raises(ValueError, match="invalid topic_id"):
        require_safe_id("a/b", "topic_id")
    assert require_safe_id("run-abc123", "run_id") == "run-abc123"
