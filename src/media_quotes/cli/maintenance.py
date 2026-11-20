"""Operator commands for review queue, retention planning and restore drills (offline)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

from ..config import Settings
from ..exceptions import ConfigError
from ..export import local_store_path, read_jsonl
from ..restore_drill import retention_readiness, verify_export_integrity
from ..risk_controls.models import ObsolescenceFlag
from ..risk_controls.obsolescence import ReviewQueue
from ..risk_controls.retention import RetentionPolicy
from .common import EXIT_FAILURE, EXIT_OK, _read_json, _write_json

QUEUE_FILE = "review-queue.json"


def _queue_path(settings: Settings) -> Path:
    return settings.audit_dir / QUEUE_FILE


def _load_queue(settings: Settings) -> dict[str, Any]:
    payload = _read_json(_queue_path(settings))
    return payload if isinstance(payload, dict) else {"flags": []}


def _flag_from_dict(data: dict[str, Any]) -> ObsolescenceFlag:
    return ObsolescenceFlag.model_validate(data)


def _do_review(
    settings: Settings,
    *,
    action: str,
    quote_id: str | None = None,
    verdict: str | None = None,
    reviewer: str = "Balgove Innovations",
    reason_code: str = "human-review",
) -> int:
    """List pending obsolete flags or decide one using ReviewQueue (fail-closed)."""
    payload = _load_queue(settings)
    flags_data = cast(list[dict[str, Any]], payload.get("flags", []))
    flags = [_flag_from_dict(item) for item in flags_data]
    queue = ReviewQueue(flags)

    if action == "list":
        pending = [
            flag.model_dump(mode="json")
            for flag in flags
            if flag.state in {"pending", "needs_evidence", "escalated"}
        ]
        print(json.dumps(pending, ensure_ascii=False, sort_keys=True, indent=2))
        return EXIT_OK
    if action != "decide":
        raise ConfigError(f"unknown review action: {action}")
    if not quote_id:
        raise ConfigError("--quote-id is required for review decide")

    chosen_verdict = verdict or ("rejected" if reason_code == "reject" else "confirmed")
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

    # Persist updated flags
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
    print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    if not dry_run:
        print("Live retention job is not available in blocked/degraded mode; no rows were touched.")
    return EXIT_OK


def _do_restore_drill(settings: Settings, *, reference: Path | None = None) -> int:
    """Compare the latest derived export with a reference export (identity + hash)."""
    today = settings.report_date
    current_path = local_store_path(settings.export_dir, today)
    if reference is None:
        files = sorted(settings.export_dir.glob("quotes-*.jsonl"), reverse=True)
        if len(files) < 2:
            print("restore drill requires two exports; pass --reference PATH")
            return EXIT_FAILURE
        reference = files[1]
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
