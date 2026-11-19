"""RSK-07 obsolescence review state machine (CHK-15)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

from .models import ObsolescenceEvidence, ObsolescenceFlag, ReviewState

VERDICTS = frozenset({"confirmed", "rejected", "escalated"})
TERMINAL_STATES = frozenset({"confirmed", "rejected"})


def triage_obsolescence(confidence: float) -> ReviewState:
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be in [0, 1]")
    if confidence >= 0.80:
        return "pending"
    if confidence >= 0.50:
        return "needs_evidence"
    return "rejected"


def review_overdue(flag: ObsolescenceFlag, now: datetime, sla_days: int = 1) -> bool:
    """One-working-day SLA using ``raised_at`` where available."""
    if flag.state not in {"pending", "needs_evidence", "escalated"}:
        return False
    raised = flag.raised_at or flag.reviewed_at or now
    return now - raised > timedelta(days=sla_days)


class ReviewQueue:
    """In-memory queue; the T6 UI/driver persists decisions."""

    def __init__(self, flags: Iterable[ObsolescenceFlag] = ()) -> None:
        self._flags: dict[str, ObsolescenceFlag] = {}
        for flag in flags:
            self.add(flag)

    def add(self, flag: ObsolescenceFlag) -> None:
        key = flag.evidence.old_quote_id
        if key in self._flags:
            raise ValueError(f"quote {key} already has an obsolescence flag")
        self._flags[key] = flag

    def decide(
        self,
        old_quote_id: str,
        verdict: str,
        *,
        reviewer: str,
        now: datetime,
        reason_code: str,
    ) -> ObsolescenceFlag:
        current = self._flags.get(old_quote_id)
        if current is None:
            raise KeyError(old_quote_id)
        if current.state in TERMINAL_STATES:
            raise ValueError(f"quote {old_quote_id} already decided: {current.state}")
        if verdict not in VERDICTS:
            raise ValueError(f"invalid verdict: {verdict}")
        if not reviewer.strip() or not reason_code.strip():
            raise ValueError("reviewer and reason_code are required")
        updated = current.model_copy(
            update={
                "state": verdict,
                "reviewer": reviewer,
                "reviewed_at": now,
                "reason_code": reason_code,
            }
        )
        self._flags[old_quote_id] = updated
        return updated

    def overdue(self, now: datetime, sla_days: int = 1) -> list[str]:
        return sorted(
            quote_id
            for quote_id, flag in self._flags.items()
            if flag.state not in TERMINAL_STATES and review_overdue(flag, now, sla_days)
        )

    def hideable_quotes(self) -> list[str]:
        return sorted(q for q, flag in self._flags.items() if flag.may_hide_quote())


def obsoletion_allowed(*, obsolete_precision: float, consecutive_failures: int) -> bool:
    """Two consecutive sub-threshold evals disable LLM obsoletion."""
    if consecutive_failures < 0:
        raise ValueError("consecutive_failures must be >= 0")
    return not (obsolete_precision < 0.90 and consecutive_failures >= 2)


__all__ = [
    "ObsolescenceEvidence",
    "ObsolescenceFlag",
    "ReviewQueue",
    "obsoletion_allowed",
    "review_overdue",
    "triage_obsolescence",
]
