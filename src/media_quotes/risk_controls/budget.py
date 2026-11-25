"""RSK-08 backfill budget gate (CHK-16)."""

from __future__ import annotations

from ..exceptions import BudgetExceededError
from .models import BackfillEstimate


def estimate_backfill(
    *,
    articles_per_source_day: float,
    sources: int,
    days: int,
    calls_per_article: float,
    price_per_call: float,
    articles_per_hour: float,
    expected_cache_hit_rate: float = 0.0,
) -> BackfillEstimate:
    rates = (
        articles_per_source_day,
        sources,
        days,
        calls_per_article,
        price_per_call,
        articles_per_hour,
    )
    if min(rates) <= 0:
        raise ValueError("all rates/counts must be positive")
    if not 0.0 <= expected_cache_hit_rate < 1.0:
        raise ValueError("cache hit rate must be in [0, 1)")
    articles = round(articles_per_source_day * sources * days)
    billed_calls = round(articles * calls_per_article * (1.0 - expected_cache_hit_rate))
    return BackfillEstimate(
        articles=articles,
        calls=billed_calls,
        cost=round(billed_calls * price_per_call, 4),
        duration_hours=round(articles / articles_per_hour, 2),
        cache_hit_rate=expected_cache_hit_rate,
    )


def budget_gate(estimate: BackfillEstimate, max_cost: float) -> str:
    import math

    if not math.isfinite(max_cost) or max_cost <= 0:
        raise ValueError("max_cost must be positive and finite")
    if estimate.cost > max_cost:
        raise BudgetExceededError(
            f"estimated cost {estimate.cost} exceeds budget {max_cost}; "
            "raise --max-cost or narrow the span"
        )
    if estimate.cost >= 0.8 * max_cost:
        return f"warning: estimated cost {estimate.cost} is >= 80% of budget {max_cost}"
    return f"within budget: {estimate.cost} <= {max_cost}"


__all__ = ["BudgetExceededError", "budget_gate", "estimate_backfill"]
