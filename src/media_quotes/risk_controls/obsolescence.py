"""RSK-07 obsolescence review state machine (CHK-15)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any, Protocol

from ..enums import APPROVED_RIGHTS, PERMITTED_ACCESS
from ..exceptions import ReviewNotFoundError, ReviewStateError
from ..models import ExportQuoteRecord
from ..text_utils import normalise_text
from .models import ObsolescenceEvidence, ObsolescenceFlag, ReviewState

VERDICTS = frozenset({"confirmed", "rejected", "escalated"})
TERMINAL_STATES = frozenset({"confirmed", "rejected"})
JUDGE_CONFIDENCE_THRESHOLD = 0.80
MAX_JUDGE_DECISIONS = 8  # bounded paid judge calls per partition, including historical replay
JUDGE_VERDICTS = {
    "accept": "confirmed",
    "accepted": "confirmed",
    "confirm": "confirmed",
    "confirmed": "confirmed",
    "reject": "rejected",
    "rejected": "rejected",
    "escalate": "escalated",
    "escalated": "escalated",
}


class JudgeProviderLike(Protocol):
    """Callable judge provider shape: one decision per task/context pair."""

    def decide(self, *, task: str, context: Mapping[str, Any]) -> Any: ...


class JudgeDecisionLike(Protocol):
    """Minimal read-only shape needed to apply a judge suggestion to the queue."""

    @property
    def verdict(self) -> str: ...

    @property
    def confidence(self) -> float: ...

    @property
    def rationale(self) -> str: ...

    @property
    def actor(self) -> str: ...

    @property
    def escalation_reason(self) -> str: ...


def _value(source: Any, name: str, default: Any) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _label(source: Any, name: str) -> str:
    value = _value(source, name, "")
    return value if isinstance(value, str) else str(value) if value is not None else ""


def pending_flags(flags: Iterable[ObsolescenceFlag]) -> list[ObsolescenceFlag]:
    """Flags the review command may still act on."""
    return [
        flag
        for flag in flags
        if flag.state not in TERMINAL_STATES
        and flag.state in {"pending", "needs_evidence", "escalated"}
    ]


def obsolescence_judge_payload(flag: ObsolescenceFlag) -> dict[str, Any]:
    """Deterministic, minimal input for task='obsolescence_review'."""
    return {
        "quote_id": flag.evidence.old_quote_id,
        "old_content_hash": flag.evidence.old_content_hash,
        "newer_article_id": flag.evidence.newer_article_id,
        "newer_content_hash": flag.evidence.newer_content_hash,
        "newer_is_first_hand": flag.evidence.newer_is_first_hand,
        "newer_rights_cleared": flag.evidence.newer_rights_cleared,
        "superseding_span": flag.evidence.superseding_span,
        "rationale": flag.evidence.rationale,
        "confidence": flag.confidence,
        "state": flag.state,
    }


def judge_obsolescence_verdict(decision: JudgeDecisionLike) -> str:
    """Fail closed: refuse/incomplete/low confidence can never confirm a flag."""
    verdict = _label(decision, "verdict").strip().lower()
    escalation = _label(decision, "escalation_reason").strip()
    try:
        confidence = float(_value(decision, "confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if verdict in {"refused", "refusal", "abstain", "abstained", "unknown", ""}:
        return "escalated"
    if escalation or confidence < JUDGE_CONFIDENCE_THRESHOLD:
        return "escalated"
    return JUDGE_VERDICTS.get(verdict, "escalated")


def flag_with_judge_provenance(
    flag: ObsolescenceFlag, decision: JudgeDecisionLike
) -> ObsolescenceFlag:
    """Attach the judge actor/rationale/confidence without changing the state machine."""
    actor = _label(decision, "actor") or "ai-judge"
    rationale = _label(decision, "rationale")
    combined = f"{flag.evidence.rationale} [ai-judge:{actor}] {rationale}".strip()
    try:
        confidence = float(_value(decision, "confidence", flag.confidence))
    except (TypeError, ValueError):
        confidence = flag.confidence
    evidence = flag.evidence.model_copy(update={"rationale": combined})
    return flag.model_copy(update={"evidence": evidence, "confidence": confidence})


def _eligible_obsolescence_pair(old: ExportQuoteRecord, newer: ExportQuoteRecord) -> bool:
    """Cheap deterministic gates that must pass before a paid judge call."""
    return (
        old.quote_id != newer.quote_id
        and old.topic_id == newer.topic_id
        and newer.publication_date >= old.publication_date
        and normalise_text(old.quote_text_original) != normalise_text(newer.quote_text_original)
        and newer.original_reporting_flag == "first-hand"
        and newer.rights_status in APPROVED_RIGHTS
        and newer.access_mode in PERMITTED_ACCESS
    )


def assess_obsolescence(
    old: ExportQuoteRecord,
    newer: ExportQuoteRecord,
    *,
    now: datetime,
    judge: JudgeDecisionLike,
) -> ObsolescenceFlag | None:
    """Build a flag only when the judge confirms a newer quote supersedes an older one."""
    if not _eligible_obsolescence_pair(old, newer):
        return None

    confidence = float(_value(judge, "confidence", 0.0))
    verdict = judge_obsolescence_verdict(judge)
    if verdict != "confirmed":
        return None
    state: ReviewState = triage_obsolescence(confidence)
    rationale = _label(judge, "rationale") or "Judge confirmed supersession."
    decision_id = f"supersession:{old.quote_id}:{newer.quote_id}"
    evidence = ObsolescenceEvidence(
        old_quote_id=old.quote_id,
        old_content_hash=old.content_hash,
        newer_article_id=newer.article_url,
        newer_content_hash=newer.content_hash,
        newer_is_first_hand=newer.original_reporting_flag == "first-hand",
        newer_rights_cleared=True,
        superseding_span=newer.quote_text_original,
        decision_id=decision_id,
        rationale=rationale or "Newer first-hand quote supersedes the earlier position.",
        new_quote_id=newer.quote_id,
    )
    return ObsolescenceFlag(
        evidence=evidence,
        confidence=confidence,
        state=state,
        raised_at=now,
        reason_code="supersession",
    )


def _candidate_judge_context(old: ExportQuoteRecord, newer: ExportQuoteRecord) -> dict[str, Any]:
    """Minimal, untrusted payload describing one supersession candidate pair."""
    return {
        "old_quote_id": old.quote_id,
        "old_content_hash": old.content_hash,
        "old_quote_text": old.quote_text_original,
        "old_publication_date": _label(old, "publication_date"),
        "newer_quote_id": newer.quote_id,
        "newer_article_id": newer.article_url,
        "newer_content_hash": newer.content_hash,
        "newer_quote_text": newer.quote_text_original,
        "newer_publication_date": _label(newer, "publication_date"),
        "newer_is_first_hand": newer.original_reporting_flag == "first-hand",
        "newer_rights_cleared": newer.rights_status in APPROVED_RIGHTS
        and newer.access_mode in PERMITTED_ACCESS,
    }


def propose_obsolescence(
    records: Sequence[ExportQuoteRecord],
    *,
    now: datetime,
    judge: JudgeDecisionLike | JudgeProviderLike,
    max_decisions: int = MAX_JUDGE_DECISIONS,
) -> list[ObsolescenceFlag]:
    """Deterministic candidate flags; a human still decides before any quote is hidden.

    Eligible pairs reach the paid judge at most ``max_decisions`` times per call. A
    precomputed decision is reused as-is (offline/tests).
    """
    if max_decisions < 0:
        raise ValueError("max_decisions must not be negative")
    proposed: list[ObsolescenceFlag] = []
    decide = getattr(judge, "decide", None)
    decisions = 0
    for index, old in enumerate(records):
        if decisions >= max_decisions:
            break
        for newer in records[index + 1 :]:
            if not _eligible_obsolescence_pair(old, newer):
                continue
            if decisions >= max_decisions:
                break
            decision: Any = judge
            if callable(decide):
                decision = decide(
                    task="obsolescence_review", context=_candidate_judge_context(old, newer)
                )
                decisions += 1
            flag = assess_obsolescence(old, newer, now=now, judge=decision)
            if flag is not None:
                proposed.append(flag)
                break
    return proposed


def triage_obsolescence(confidence: float) -> ReviewState:
    if not 0.0 <= confidence <= 1.0:
        raise ValueError("confidence must be in [0, 1]")
    if confidence >= 0.80:
        return "pending"
    if confidence >= 0.50:
        return "needs_evidence"
    return "rejected"


def review_overdue(flag: ObsolescenceFlag, now: datetime, sla_days: int = 1) -> bool:
    """One calendar-day SLA using ``raised_at`` where available."""
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
            raise ReviewStateError(f"quote {key} already has an obsolescence flag")
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
            raise ReviewNotFoundError(f"quote {old_quote_id} not found in review queue")
        if current.state in TERMINAL_STATES:
            raise ReviewStateError(f"quote {old_quote_id} already decided: {current.state}")
        if verdict not in VERDICTS:
            raise ReviewStateError(f"invalid verdict: {verdict}")
        if not reviewer.strip() or not reason_code.strip():
            raise ReviewStateError("reviewer and reason_code are required")
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

    def hideable_quotes(self, *, now: datetime, hold_hours: int = 24) -> list[str]:
        return sorted(
            q
            for q, flag in self._flags.items()
            if flag.may_hide_quote(now=now, hold_hours=hold_hours)
        )


def obsoletion_allowed(*, obsolete_precision: float, consecutive_failures: int) -> bool:
    """Two consecutive sub-threshold evals disable LLM obsoletion."""
    if consecutive_failures < 0:
        raise ValueError("consecutive_failures must be >= 0")
    return not (obsolete_precision < 0.90 and consecutive_failures >= 2)


__all__ = [
    "MAX_JUDGE_DECISIONS",
    "ObsolescenceEvidence",
    "ObsolescenceFlag",
    "ReviewQueue",
    "assess_obsolescence",
    "flag_with_judge_provenance",
    "judge_obsolescence_verdict",
    "obsolescence_judge_payload",
    "obsoletion_allowed",
    "pending_flags",
    "propose_obsolescence",
    "review_overdue",
    "triage_obsolescence",
]
