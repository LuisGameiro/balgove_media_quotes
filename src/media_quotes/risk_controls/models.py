"""Strict data models for the RSK-07/08/11 policy boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

STRICT = ConfigDict(strict=True, extra="forbid")
ReviewState = Literal["pending", "needs_evidence", "confirmed", "rejected", "escalated"]


class ObsolescenceEvidence(BaseModel):
    """Bundle required before a quote may be hidden as obsolete (RSK-07)."""

    model_config = STRICT

    old_quote_id: str
    old_content_hash: str
    newer_article_id: str
    newer_content_hash: str
    newer_is_first_hand: bool
    newer_rights_cleared: bool
    superseding_span: str = Field(min_length=1)
    decision_id: str
    rationale: str = Field(min_length=1)

    @property
    def is_primary_evidence(self) -> bool:
        return self.newer_is_first_hand and self.newer_rights_cleared


class ObsolescenceFlag(BaseModel):
    """One supersession proposal, with the reviewer verdict still open or recorded."""

    model_config = STRICT

    evidence: ObsolescenceEvidence
    confidence: float = Field(ge=0.0, le=1.0)
    state: ReviewState = "pending"
    reviewer: str | None = None
    reviewed_at: datetime | None = None
    raised_at: datetime | None = None
    reason_code: str | None = None

    def may_hide_quote(self) -> bool:
        return (
            self.state == "confirmed"
            and self.evidence.is_primary_evidence
            and self.reviewer is not None
            and self.reviewed_at is not None
        )


@dataclass(frozen=True, slots=True)
class BackfillEstimate:
    """Estimate produced by `mq backfill --dry-run` (RSK-08)."""

    articles: int
    calls: int
    cost: float
    duration_hours: float
    cache_hit_rate: float = 0.0


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Defaults from docs/02 §4 and docs/10 §3; audit rows are never dropped."""

    blob_days: int = 180
    log_days: int = 90
    max_transaction_seconds: int = 60
    audit_rows_dropped: bool = False

    def prune_cutoffs(self, today: date) -> tuple[date, date]:
        return today - timedelta(days=self.blob_days), today - timedelta(days=self.log_days)

    def allow_prune(self, blob_date: date, today: date, *, tombstone_written: bool) -> bool:
        if self.audit_rows_dropped:
            return False
        cutoff, _ = self.prune_cutoffs(today)
        return blob_date < cutoff and tombstone_written

    def lock_ok(self, transaction_seconds: int) -> bool:
        return transaction_seconds < self.max_transaction_seconds


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    """Which partitions to keep hot/detach and the cutoffs for blobs/logs."""

    blob_cutoff: date
    log_cutoff: date
    detach: tuple[str, ...]
    keep_hot: tuple[str, ...]


def utc_now() -> datetime:
    """Single clock helper; production code should pass an explicit `now` in tests."""
    return datetime.now(UTC)
