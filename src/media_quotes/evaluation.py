"""Acceptance thresholds from docs/01 §7 — one contract for file and Postgres eval runs.

Pure functions only: thresholds and pass/fail are identical regardless of where the metrics
were computed. `mq eval` persists the result; a future Postgres eval run reuses the same code.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

STRICT = ConfigDict(strict=True, extra="forbid")


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

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()


def evaluate_metrics(
    metrics: Mapping[str, Any], thresholds: AcceptanceThresholds
) -> dict[str, Any]:
    """Return a copy of `metrics` with `thresholds`, `pass` and failing check names."""
    minimums = thresholds.as_dict()
    values = {name: float(metrics.get(name, 0.0)) for name in minimums}
    passed = {
        name: (value <= minimums[name] if name == "false_quote_rate" else value >= minimums[name])
        for name, value in values.items()
    }
    result = dict(metrics)
    result["thresholds"] = thresholds.as_dict()
    result["pass"] = all(passed.values())
    result["failed_checks"] = sorted(name for name, ok in passed.items() if not ok)
    return result
