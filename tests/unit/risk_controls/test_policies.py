"""CHK-15/16/17 — unit level checks for the RSK-07/08/11 mitigation plan (docs/10)."""

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from media_quotes.models import ExportQuoteRecord
from media_quotes.risk_controls import (
    BackfillEstimate,
    BudgetExceededError,
    ObsolescenceEvidence,
    ObsolescenceFlag,
    RetentionPolicy,
    ReviewQueue,
    budget_gate,
    estimate_backfill,
    obsoletion_allowed,
    partition_name,
    retention_plan,
    review_overdue,
    triage_obsolescence,
)


def evidence(*, first_hand: bool = True, cleared: bool = True) -> ObsolescenceEvidence:
    return ObsolescenceEvidence(
        old_quote_id="Q-20261101-0001",
        old_content_hash="a" * 64,
        newer_article_id="art-1",
        newer_content_hash="b" * 64,
        newer_is_first_hand=first_hand,
        newer_rights_cleared=cleared,
        superseding_span="the newer fact",
        decision_id="dec-1",
        rationale="superseded by a first-hand report",
    )


# --- CHK-15: obsolescence review -----------------------------------------------------------


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [(0.9, "pending"), (0.8, "pending"), (0.5, "needs_evidence"), (0.49, "rejected")],
)
def test_triage_thresholds(confidence: float, expected: str) -> None:
    assert triage_obsolescence(confidence) == expected


def test_quote_stays_visible_until_human_confirms() -> None:
    flag = ObsolescenceFlag(evidence=evidence(), confidence=0.95)
    assert not flag.may_hide_quote(now=datetime.now(UTC))
    assert triage_obsolescence(flag.confidence) == "pending"


def test_confirmed_flag_hides_only_with_evidence_and_reviewer() -> None:
    now = datetime.now(UTC)
    good = ObsolescenceFlag(
        evidence=evidence(), confidence=0.95, state="confirmed", reviewer="BI", reviewed_at=now
    )
    assert not good.may_hide_quote(now=now)
    assert not good.may_hide_quote(now=now + timedelta(hours=1))
    assert good.may_hide_quote(now=now + timedelta(hours=25))

    relayed = ObsolescenceFlag(
        evidence=evidence(first_hand=False),
        confidence=0.95,
        state="confirmed",
        reviewer="BI",
        reviewed_at=now,
    )
    assert not relayed.may_hide_quote(now=now)

    unreviewed = ObsolescenceFlag(evidence=evidence(), confidence=0.95, state="confirmed")
    assert not unreviewed.may_hide_quote(now=now)


def test_propose_obsolescence_builds_evidence_and_holds_for_review() -> None:
    from media_quotes.risk_controls.obsolescence import propose_obsolescence

    def record(quote_id: str, text: str, day: date) -> ExportQuoteRecord:
        return ExportQuoteRecord.model_construct(
            quote_id=quote_id,
            quote_text_original=text,
            topic_id="ukraine-war",
            section_id="war_intensity",
            publication_date=day,
            original_reporting_flag="first-hand",
            rights_status="license_held",
            access_mode="licensed_database",
            article_url="https://example.invalid/a",
            content_hash="a" * 64,
        )

    older = record("Q-20261101-0001", "Talks continue tomorrow", date(2026, 11, 1))
    newer = record("Q-20261119-0001", "Talks have been paused", date(2026, 11, 19))
    now = datetime(2026, 11, 19, 12, tzinfo=UTC)

    class Judge:
        verdict = "confirmed"
        confidence = 0.95
        rationale = "Newer first-hand statement supersedes the earlier position."
        actor = "bi"
        escalation_reason = ""

    class NoJudge:
        verdict = "escalate"
        confidence = 0.0
        rationale = ""
        actor = "bi"
        escalation_reason = "unclear"

    assert propose_obsolescence([older, newer], now=now, judge=NoJudge()) == []
    flags = propose_obsolescence([older, newer], now=now, judge=Judge())
    assert len(flags) == 1
    assert flags[0].evidence.old_quote_id == older.quote_id
    assert flags[0].evidence.is_primary_evidence
    assert flags[0].state in {"pending", "needs_evidence"}
    assert not flags[0].may_hide_quote(now=now)


def test_propose_obsolescence_calls_provider_for_same_speaker_only() -> None:
    from media_quotes.risk_controls.obsolescence import propose_obsolescence

    def record(quote_id: str, text: str, day: date) -> ExportQuoteRecord:
        return ExportQuoteRecord.model_construct(
            quote_id=quote_id,
            quote_text_original=text,
            topic_id="ukraine-war",
            section_id="war_intensity",
            publication_date=day,
            original_reporting_flag="first-hand",
            rights_status="license_held",
            access_mode="licensed_database",
            article_url=f"https://example.invalid/{quote_id}",
            content_hash="a" * 64,
        )

    class Provider:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        def decide(self, *, task: str, context: Mapping[str, Any]) -> dict[str, object]:
            self.calls.append({"task": task, **dict(context)})
            return {
                "verdict": "confirm",
                "confidence": 0.95,
                "rationale": "The newer statement supersedes the earlier position.",
                "actor": "test-judge",
                "escalation_reason": "",
            }

    older = record("Q-20261101-0001", "Talks continue tomorrow", date(2026, 11, 1))
    newer = record("Q-20261119-0001", "Talks have been paused", date(2026, 11, 19))
    other = record("Q-20261119-0002", "Aid deliveries have increased", date(2026, 11, 19))
    provider = Provider()
    now = datetime(2026, 11, 19, 12, tzinfo=UTC)

    flags = propose_obsolescence(
        [older, newer, other],
        now=now,
        judge=provider,
        speakers={
            older.quote_id: "Alex Morgan",
            newer.quote_id: "Alex Morgan",
            other.quote_id: "Sam Lee",
        },
    )

    assert [flag.evidence.old_quote_id for flag in flags] == [older.quote_id]
    assert len(provider.calls) == 1
    assert provider.calls[0]["task"] == "obsolescence_review"
    assert provider.calls[0]["old_quote_id"] == older.quote_id
    assert provider.calls[0]["newer_quote_id"] == newer.quote_id


def test_evidence_bundle_rejects_empty_span_and_coercion() -> None:
    with pytest.raises(ValidationError):
        evidence().__class__(
            old_quote_id="Q",
            old_content_hash="a" * 64,
            newer_article_id="art-1",
            newer_content_hash="b" * 64,
            newer_is_first_hand=True,
            newer_rights_cleared="true",  # type: ignore[arg-type]
            superseding_span="",
            decision_id="dec-1",
            rationale="r",
        )


def test_review_overdue_sla() -> None:
    now = datetime.now(UTC)
    fresh = ObsolescenceFlag(evidence=evidence(), confidence=0.9, state="pending")
    assert not review_overdue(fresh, now)
    stale = ObsolescenceFlag(
        evidence=evidence(), confidence=0.9, state="pending", reviewed_at=now - timedelta(days=2)
    )
    assert review_overdue(stale, now)


def test_review_queue_state_machine_and_hideable() -> None:
    now = datetime.now(UTC)
    queue = ReviewQueue([ObsolescenceFlag(evidence=evidence(), confidence=0.95)])
    assert queue.hideable_quotes(now=now) == []
    updated = queue.decide(
        "Q-20261101-0001", "confirmed", reviewer="BI", now=now, reason_code="superseded"
    )
    assert not updated.may_hide_quote(now=now)
    assert queue.hideable_quotes(now=now + timedelta(hours=25)) == ["Q-20261101-0001"]
    with pytest.raises(ValueError):
        queue.decide("Q-20261101-0001", "rejected", reviewer="BI", now=now, reason_code="late")


def test_review_queue_duplicates_and_missing() -> None:
    flag = ObsolescenceFlag(evidence=evidence(), confidence=0.9)
    queue = ReviewQueue([flag])
    with pytest.raises(ValueError):
        queue.add(flag)
    with pytest.raises(KeyError):
        queue.decide("missing", "confirmed", reviewer="BI", now=datetime.now(UTC), reason_code="r")


@pytest.mark.parametrize(
    ("precision", "failures", "expected"),
    [(0.95, 0, True), (0.90, 1, True), (0.89, 1, True), (0.89, 2, False), (0.95, 2, True)],
)
def test_drift_guard(precision: float, failures: int, expected: bool) -> None:
    result = obsoletion_allowed(obsolete_precision=precision, consecutive_failures=failures)
    assert result is expected


def test_review_queue_overdue_ids() -> None:
    now = datetime.now(UTC)
    stale = ObsolescenceFlag(
        evidence=evidence(), confidence=0.9, state="pending", reviewed_at=now - timedelta(days=2)
    )
    assert ReviewQueue([stale]).overdue(now) == ["Q-20261101-0001"]


# --- CHK-16: backfill budget guard ---------------------------------------------------------


def test_estimate_is_linear_and_documented() -> None:
    est = estimate_backfill(
        articles_per_source_day=10,
        sources=26,
        days=50,
        calls_per_article=2,
        price_per_call=0.01,
        articles_per_hour=100,
    )
    assert est.articles == 13_000
    assert est.calls == 26_000
    assert est.cost == 260.0
    assert est.duration_hours == 130.0


def test_cache_reduces_billed_calls() -> None:
    no_cache = estimate_backfill(
        articles_per_source_day=10,
        sources=1,
        days=10,
        calls_per_article=2,
        price_per_call=0.01,
        articles_per_hour=100,
    )
    cached = estimate_backfill(
        articles_per_source_day=10,
        sources=1,
        days=10,
        calls_per_article=2,
        price_per_call=0.01,
        articles_per_hour=100,
        expected_cache_hit_rate=0.6,
    )
    assert cached.calls == 80  # 100 articles * 2 calls * (1 - 0.6)
    assert cached.cost < no_cache.cost


def test_budget_gate_warns_and_blocks() -> None:
    est = BackfillEstimate(articles=10, calls=20, cost=85.0, duration_hours=1)
    assert "warning" in budget_gate(est, 100.0)
    assert "within budget" in budget_gate(est, 200.0)
    with pytest.raises(BudgetExceededError, match="raise --max-cost or narrow the span"):
        budget_gate(est, 50.0)


# --- CHK-17: retention and lock bounds -----------------------------------------------------


def test_retention_cutoffs_and_prune_conditions() -> None:
    policy = RetentionPolicy()
    today = date(2026, 11, 19)
    blob_cutoff, log_cutoff = policy.prune_cutoffs(today)
    assert blob_cutoff == date(2026, 5, 23)
    assert log_cutoff == date(2026, 8, 21)
    assert not policy.allow_prune(date(2026, 1, 1), today, tombstone_written=False)
    assert policy.allow_prune(date(2026, 1, 1), today, tombstone_written=True)
    assert not policy.allow_prune(date(2026, 11, 1), today, tombstone_written=True)


def test_audit_rows_never_dropped() -> None:
    policy = RetentionPolicy(audit_rows_dropped=True)
    assert not policy.allow_prune(date(2026, 1, 1), date(2026, 11, 19), tombstone_written=True)


@pytest.mark.parametrize(("seconds", "ok"), [(10, True), (59, True), (60, False), (120, False)])
def test_lock_age_bound(seconds: int, ok: bool) -> None:
    assert RetentionPolicy().lock_ok(seconds) is ok


def test_partition_name_and_retention_plan() -> None:
    assert partition_name("audit_events", date(2026, 11, 1)) == "audit_events_2026_11"
    with pytest.raises(ValueError):
        partition_name("audit;drop", date(2026, 11, 1))
    plan = retention_plan(
        RetentionPolicy(), date(2026, 11, 20), [date(2026, 10, 1), date(2026, 11, 1)]
    )
    assert plan.blob_cutoff == date(2026, 5, 24)
    assert plan.log_cutoff == date(2026, 8, 22)
    assert all(name.endswith("2026_10") for name in plan.detach)
    assert all(name.endswith("2026_11") for name in plan.keep_hot)
    assert len(plan.detach) == 1
    assert len(plan.keep_hot) == 1
