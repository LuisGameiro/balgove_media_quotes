"""Coverage counters stored in pipeline_runs.stats as JSONB.

Every run records discovered / fetched / parsed / rejected / failed / skipped counts per
source and per day. This turns 'all material articles detected' into a measurable claim:
you can see exactly what was enumerated and what was processed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

Outcome = Literal["discovered", "fetched", "parsed", "rejected", "failed", "skipped"]


class CoverageStats(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    discovered: int = 0
    fetched: int = 0
    parsed: int = 0
    rejected: int = 0
    failed: int = 0
    skipped: int = 0

    def record(self, outcome: Outcome, count: int = 1) -> None:
        setattr(self, outcome, getattr(self, outcome) + count)

    def to_dict(self) -> dict[str, int]:
        return self.model_dump()
