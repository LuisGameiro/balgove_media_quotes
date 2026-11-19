"""Extra coverage for validation branches in the risk-control policies."""

from datetime import date, datetime

import pytest

from media_quotes.risk_controls import (
    BackfillEstimate,
    ObsolescenceEvidence,
    ObsolescenceFlag,
    budget_gate,
    estimate_backfill,
    review_overdue,
    triage_obsolescence,
    utc_now,
)


def _evidence() -> ObsolescenceEvidence:
    return ObsolescenceEvidence(
        old_quote_id="Q",
        old_content_hash="a" * 64,
        newer_article_id="a1",
        newer_content_hash="b" * 64,
        newer_is_first_hand=True,
        newer_rights_cleared=True,
        superseding_span="new fact",
        decision_id="d1",
        rationale="reason",
    )


def test_triage_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        triage_obsolescence(1.1)


def test_review_overdue_is_false_for_closed_states() -> None:
    flag = ObsolescenceFlag(evidence=_evidence(), confidence=0.9, state="confirmed")
    assert not review_overdue(flag, datetime.now())


def test_estimate_backfill_rejects_non_positive_rates() -> None:
    with pytest.raises(ValueError, match="rates"):
        estimate_backfill(
            articles_per_source_day=0,
            sources=1,
            days=1,
            calls_per_article=1,
            price_per_call=0.1,
            articles_per_hour=1,
        )


def test_estimate_backfill_rejects_invalid_cache_rate() -> None:
    with pytest.raises(ValueError, match="cache hit rate"):
        estimate_backfill(
            articles_per_source_day=1,
            sources=1,
            days=1,
            calls_per_article=1,
            price_per_call=0.1,
            articles_per_hour=1,
            expected_cache_hit_rate=1.0,
        )


def test_budget_gate_rejects_non_positive_cap() -> None:
    estimate = BackfillEstimate(articles=1, calls=1, cost=1.0, duration_hours=1.0)
    with pytest.raises(ValueError, match="max_cost"):
        budget_gate(estimate, 0.0)


def test_utc_now_returns_datetime() -> None:
    assert isinstance(utc_now(), datetime)
    assert date(2000, 1, 1) < utc_now().date()
