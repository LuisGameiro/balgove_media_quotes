"""Acceptance thresholds from docs/01 §7 — one contract for file and Postgres eval runs.

Pure functions only: thresholds and pass/fail are identical regardless of where the metrics
were computed. `mq eval` persists the result; a future Postgres eval run reuses the same code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

STRICT = ConfigDict(strict=True, extra="forbid")

# Bump when metric definitions or their computation change, so a re-eval writes a new
# eval_runs row instead of silently keeping stale metrics for the same gold set and date.
EVAL_CONTRACT_VERSION = "eval-contract-v1"


class AcceptanceThresholds(BaseModel):
    """Minimum values; false_quote_rate is a maximum (0.0 required)."""

    model_config = STRICT

    article_recall: float = Field(default=0.90, ge=0.0, le=1.0)
    quote_recall: float = Field(default=0.90, ge=0.0, le=1.0)
    span_exact_match: float = Field(default=0.95, ge=0.0, le=1.0)
    metadata_accuracy: float = Field(default=0.95, ge=0.0, le=1.0)
    section_accuracy: float = Field(default=0.90, ge=0.0, le=1.0)
    false_quote_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    obsolete_precision: float = Field(default=0.90, ge=0.0, le=1.0)
    judge_kappa: float = Field(default=0.70, ge=0.0, le=1.0)
    # Topic/language/category acceptance criteria. New metrics are only
    # enforced when a report actually measured them so older gold sets stay comparable.
    topic_recall: float = Field(default=0.75, ge=0.0, le=1.0)
    topic_precision: float = Field(default=0.90, ge=0.0, le=1.0)
    topic_false_positive_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    language_accuracy: float = Field(default=0.80, ge=0.0, le=1.0)
    category_coverage: float = Field(default=0.80, ge=0.0, le=1.0)

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


def obsolete_precision(
    confirmed_obsolete: Sequence[str], labelled_obsolete: Sequence[str]
) -> float:
    """Precision of human-confirmed obsolescence against the held-out gold labels."""
    labelled = set(labelled_obsolete)
    if not confirmed_obsolete:
        return 0.0
    correct = sum(1 for quote_id in confirmed_obsolete if quote_id in labelled)
    return round(correct / len(confirmed_obsolete), 4)


MAXIMUM_METRICS = frozenset({"false_quote_rate", "topic_false_positive_rate"})
NEW_METRICS = frozenset(
    {
        "topic_recall",
        "topic_precision",
        "topic_false_positive_rate",
        "language_accuracy",
        "category_coverage",
    }
)


def evaluate_metrics(
    metrics: Mapping[str, Any], thresholds: AcceptanceThresholds
) -> dict[str, Any]:
    """Return a copy of `metrics` with `thresholds`, `pass` and failing check names."""
    minimums = thresholds.as_dict()
    values: dict[str, float] = {}
    for name in minimums:
        if name in MAXIMUM_METRICS:
            # A missing maximum metric must fail closed, never be skipped as a new metric.
            values[name] = float(metrics.get(name, 1.0))
        elif name in NEW_METRICS and name not in metrics:
            continue
        else:
            values[name] = float(metrics.get(name, 0.0))
    passed = {
        name: (value <= minimums[name] if name in MAXIMUM_METRICS else value >= minimums[name])
        for name, value in values.items()
    }
    result = dict(metrics)
    result["thresholds"] = thresholds.as_dict()
    result["pass"] = all(passed.values())
    result["failed_checks"] = sorted(name for name, ok in passed.items() if not ok)
    return result
