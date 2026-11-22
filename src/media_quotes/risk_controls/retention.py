"""RSK-11 retention and partition planning (CHK-17)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from .models import RetentionPlan, RetentionPolicy


def partition_name(table: str, month: date) -> str:
    if not table or any(not char.isalnum() and char != "_" for char in table):
        raise ValueError("table must be a simple identifier")
    return f"{table}_{month:%Y_%m}"


def retention_plan(policy: RetentionPolicy, today: date, months: Iterable[date]) -> RetentionPlan:
    blob_cutoff, log_cutoff = policy.prune_cutoffs(today)
    current = (today.year, today.month)
    detach: list[str] = []
    keep: list[str] = []
    for month in sorted(set(months)):
        for table in ("audit_events", "llm_decisions"):
            name = partition_name(table, month)
            if (month.year, month.month) < current:
                detach.append(name)
            else:
                keep.append(name)
    return RetentionPlan(blob_cutoff, log_cutoff, tuple(detach), tuple(keep))


__all__ = ["RetentionPlan", "RetentionPolicy", "partition_name", "retention_plan"]
