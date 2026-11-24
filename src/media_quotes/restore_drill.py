"""F2 — offline restore drill and retention readiness (docs/12 Phase F).

No live Postgres or load generator required: compares deterministic quote ids and content
hashes between an exported snapshot and a restored copy, and reports the retention/lock plan
that a live load test must satisfy before RSK-11 can be closed.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from .models import ExportQuoteRecord
from .risk_controls.retention import RetentionPolicy, retention_plan


def verify_export_integrity(
    original: Sequence[ExportQuoteRecord],
    restored: Sequence[ExportQuoteRecord],
) -> tuple[bool, list[str]]:
    """Compare identity and content hashes; returns (ok, reasons)."""
    original_map = {item.quote_id: item.content_hash for item in original}
    restored_map = {item.quote_id: item.content_hash for item in restored}
    reasons: list[str] = []
    missing = sorted(set(original_map) - set(restored_map))
    extra = sorted(set(restored_map) - set(original_map))
    changed = sorted(
        quote_id
        for quote_id in set(original_map) & set(restored_map)
        if original_map[quote_id] != restored_map[quote_id]
    )
    if missing:
        reasons.append(f"missing:{','.join(missing[:5])}")
    if extra:
        reasons.append(f"extra:{','.join(extra[:5])}")
    if changed:
        reasons.append(f"hash_changed:{','.join(changed[:5])}")
    return (not reasons), reasons


def retention_readiness(
    policy: RetentionPolicy, today: date, partitions: Sequence[date], *, longest_txn_seconds: int
) -> dict[str, Any]:
    """Machine-readable readiness report for the live retention job / load test."""
    plan = retention_plan(policy, today, partitions)
    return {
        "blob_cutoff": plan.blob_cutoff.isoformat(),
        "log_cutoff": plan.log_cutoff.isoformat(),
        "partitions_to_detach": list(plan.detach),
        "partitions_hot": list(plan.keep_hot),
        "longest_txn_ok": policy.lock_ok(longest_txn_seconds),
        "audit_rows_dropped": policy.audit_rows_dropped,
    }
