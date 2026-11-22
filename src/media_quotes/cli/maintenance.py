"""Operator commands for review queue, retention planning and restore drills (offline)."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ..config import DEFAULT_TOPIC_ID, Settings
from ..exceptions import ConfigError
from ..export import local_store_path, read_jsonl
from ..pipeline import (
    JudgeDecisionSnapshot,
    JudgeProvider,
    ask_judge,
    make_judge,
    normalise_judge_decision,
)
from ..restore_drill import retention_readiness, verify_export_integrity
from ..risk_controls.models import ObsolescenceEvidence, ObsolescenceFlag
from ..risk_controls.obsolescence import (
    ReviewQueue,
    flag_with_judge_provenance,
    judge_obsolescence_verdict,
    obsolescence_judge_payload,
    pending_flags,
)
from ..risk_controls.retention import RetentionPolicy
from .common import EXIT_FAILURE, EXIT_OK, _read_json, _write_json, load_review_flags

QUEUE_FILE = "review-queue.json"


def _queue_path(settings: Settings) -> Path:
    return settings.audit_dir / QUEUE_FILE


def _load_queue(settings: Settings) -> dict[str, Any]:
    payload = _read_json(_queue_path(settings))
    return payload if isinstance(payload, dict) else {"flags": []}


@contextmanager
def _queue_lock(path: Path) -> Iterator[None]:
    """Keep the review-queue read-modify-write to one process at a time."""
    lock = path.with_name(f".{path.name}.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 5.0
    while True:
        try:
            handle = lock.open("x", encoding="utf-8")
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                # shortcut: a crashed process leaves a lock; add a TTL if this recurs
                raise ConfigError("review queue is locked by another process") from None
            time.sleep(0.05)
    try:
        yield
    finally:
        handle.close()
        lock.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class JudgeSuggestion:
    verdict: str
    decision: JudgeDecisionSnapshot

    def as_json(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "confidence": self.decision.confidence,
            "rationale": self.decision.rationale,
            "actor": self.decision.actor,
            "model_snapshot": self.decision.model_snapshot,
            "prompt_version": self.decision.prompt_version,
            "input_hash": self.decision.input_hash,
            "output_hash": self.decision.output_hash,
            "escalation_reason": self.decision.escalation_reason,
        }


def _judge_mode(settings: Settings) -> str:
    return str(getattr(settings, "ai_judge_mode", "off")).strip().lower()


def _review_judge(settings: Settings) -> JudgeProvider | None:
    if _judge_mode(settings) not in {"advisory", "decision"}:
        return None
    try:
        return make_judge(settings)
    except (ImportError, ValueError, KeyError, TypeError):
        return None


def _judge_suggestions(
    settings: Settings, judge: JudgeProvider, flags: list[ObsolescenceFlag]
) -> dict[str, JudgeSuggestion]:
    suggestions: dict[str, JudgeSuggestion] = {}
    for flag in pending_flags(flags):
        quote_id = flag.evidence.old_quote_id
        try:
            decision = normalise_judge_decision(
                ask_judge(
                    judge,
                    task="obsolescence_review",
                    payload=obsolescence_judge_payload(flag),
                )
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            decision = JudgeDecisionSnapshot(
                task="obsolescence_review",
                verdict="escalated",
                confidence=0.0,
                rationale=f"judge unavailable: {exc}",
                actor="ai-judge",
                model_snapshot=str(getattr(settings, "ai_judge_model_snapshot", "") or ""),
                prompt_version=str(getattr(settings, "judge_prompt_version", "") or ""),
                input_hash="",
                output_hash="",
                escalation_reason=str(exc),
            )
        verdict = judge_obsolescence_verdict(decision)
        suggestions[quote_id] = JudgeSuggestion(verdict=verdict, decision=decision)
    return suggestions


def _do_review(
    settings: Settings,
    *,
    action: str,
    quote_id: str | None = None,
    verdict: str | None = None,
    reviewer: str = "Balgove Innovations",
    reason_code: str = "human-review",
) -> int:
    """List, seed, or decide pending obsolete flags using ReviewQueue (fail-closed)."""
    payload = _load_queue(settings)
    flags = load_review_flags(settings)
    mode = _judge_mode(settings)
    judge = _review_judge(settings) if action in {"list", "decide"} else None
    if judge is None:
        suggestions = {}
    elif action == "decide":
        # Only the flag being decided needs a judge call; the rest of the queue does not.
        selected = [flag for flag in flags if flag.evidence.old_quote_id == quote_id]
        suggestions = _judge_suggestions(settings, judge, selected)
    else:
        suggestions = _judge_suggestions(settings, judge, flags)

    if action == "list":
        pending = []
        for flag in pending_flags(flags):
            item = flag.model_dump(mode="json")
            suggestion = suggestions.get(flag.evidence.old_quote_id)
            if suggestion is not None:
                item["ai_judge"] = suggestion.as_json()
            pending.append(item)
        print(json.dumps(pending, ensure_ascii=False, sort_keys=True, indent=2))
        return EXIT_OK

    if action == "seed":
        with _queue_lock(_queue_path(settings)):
            payload = _load_queue(settings)
            flags = load_review_flags(settings)
            target_id = quote_id or "Q-20261119-0001"
            if any(flag.evidence.old_quote_id == target_id for flag in flags):
                print(f"review flag already exists: {target_id}")
                return EXIT_OK
            sample_flag = ObsolescenceFlag(
                evidence=ObsolescenceEvidence(
                    old_quote_id=target_id,
                    old_content_hash="a" * 64,
                    newer_article_id="art-2026-11-19-02",
                    newer_content_hash="b" * 64,
                    newer_is_first_hand=True,
                    newer_rights_cleared=True,
                    superseding_span="Negotiations have been paused pending security verification.",
                    decision_id="dec-seed-001",
                    rationale="Newer official statement supersedes earlier negotiation position.",
                ),
                confidence=0.95,
                raised_at=datetime.now(UTC),
                state="pending",
            )
            flags.append(sample_flag)
            payload["flags"] = [flag.model_dump(mode="json") for flag in flags]
            _write_json(_queue_path(settings), payload)
            print(f"review flag seeded: {target_id} -> pending")
            return EXIT_OK

    if action != "decide":
        raise ConfigError(f"unknown review action: {action}")
    if not quote_id:
        raise ConfigError("--quote-id is required for review decide")

    chosen_verdict = verdict or ("rejected" if reason_code == "reject" else "confirmed")
    if mode == "advisory" and quote_id in suggestions:
        print(
            "ai-judge advisory: "
            + json.dumps(suggestions[quote_id].as_json(), ensure_ascii=False, sort_keys=True)
        )

    with _queue_lock(_queue_path(settings)):
        # Re-read under the lock so a second operator's verdict is not overwritten.
        payload = _load_queue(settings)
        flags = load_review_flags(settings)
        if mode == "decision" and quote_id in suggestions:
            suggestion = suggestions[quote_id]
            chosen_verdict = suggestion.verdict
            actor = suggestion.decision.actor or "ai-judge"
            reviewer = actor
            reason_code = "ai-judge"
            flags = [
                flag_with_judge_provenance(flag, suggestion.decision)
                if flag.evidence.old_quote_id == quote_id
                else flag
                for flag in flags
            ]

        queue = ReviewQueue(flags)
        now = datetime.now(UTC)
        try:
            decided_flag = queue.decide(
                quote_id,
                chosen_verdict,
                reviewer=reviewer,
                now=now,
                reason_code=reason_code,
            )
        except KeyError:
            print(f"no pending flag for {quote_id}", end="\n")
            return EXIT_FAILURE
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc

        updated_list = []
        for flag in flags:
            if flag.evidence.old_quote_id == quote_id:
                updated_list.append(decided_flag.model_dump(mode="json"))
            else:
                updated_list.append(flag.model_dump(mode="json"))
        payload["flags"] = updated_list
        _write_json(_queue_path(settings), payload)
        print(f"review recorded: {quote_id} -> {chosen_verdict}")
        return EXIT_OK


def _do_retention(settings: Settings, *, dry_run: bool) -> int:
    """Print the retention/lock readiness plan; no DDL is executed."""
    today: date = settings.report_date
    months = [date(today.year - 1, 1, 1), date(today.year, today.month, 1)]
    report = retention_readiness(RetentionPolicy(), today, months, longest_txn_seconds=0)
    report.pop("longest_txn_ok", None)  # no live measurement exists in offline mode
    if settings.persist_to_postgres:
        from ..db.repository import sync_storage_partitions
        from ..pipeline import _default_session_context

        with _default_session_context(settings) as session:
            observed = sync_storage_partitions(session)
        report["observed_partitions"] = observed
        print(f"Observed {observed} audit month(s) in storage_partitions (existing rows kept).")
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    print("Lock duration is not measured in offline mode.")
    if not dry_run:
        print("Live retention job is not available in blocked/degraded mode; no rows were touched.")
    return EXIT_OK


def _do_restore_drill(
    settings: Settings,
    *,
    report_date: date | None = None,
    reference: Path | None = None,
    topic_id: str | None = None,
) -> int:
    """Compare the latest derived export with a reference export (identity + hash)."""
    today = report_date or settings.report_date
    active_topic = str(topic_id or getattr(settings, "active_topic", DEFAULT_TOPIC_ID))
    root = local_store_path(settings.export_dir, today, topic_id=active_topic).parent
    current_path = local_store_path(settings.export_dir, today, topic_id=active_topic)
    files = sorted(root.glob("quotes-*.jsonl"), reverse=True)
    if reference is None:
        if len(files) < 2:
            print("restore drill requires two exports; pass --reference PATH")
            return EXIT_FAILURE
        current_path = files[0]
        reference = files[1]
    elif not current_path.is_file() and files:
        current_path = files[0]

    if not current_path.is_file() or not reference.is_file():
        print(f"restore drill missing export: {current_path} or {reference}")
        return EXIT_FAILURE
    current = read_jsonl(current_path)
    restored = read_jsonl(reference)
    ok, reasons = verify_export_integrity(current, restored)
    print(f"restore drill: {'PASS' if ok else 'FAIL'} ({current_path} vs {reference})")
    for reason in reasons:
        print(f"  {reason}")
    return EXIT_OK if ok else EXIT_FAILURE
