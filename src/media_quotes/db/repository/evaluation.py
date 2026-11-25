"""Evaluation runs, gold-set labels and LLM decision-cache persistence."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..tables import decision_cache_table, eval_runs_table, gold_set_labels_table
from .common import _inserted_count, _jsonable, _run_uuid

if TYPE_CHECKING:
    from ...gold_set import GoldSet


def record_eval_run(
    session: Session,
    *,
    gold_set_version: str,
    report_date: date,
    metrics: dict[str, Any],
    thresholds: dict[str, Any],
    passed: bool,
    trace_id: str | None = None,
    commit: bool = True,
) -> str:
    """Append one evaluation result; eval ids are stable per gold set + date."""
    from ...evaluation import EVAL_CONTRACT_VERSION

    eval_split = str(metrics.get("evaluated_split") or "all")
    eval_id_seed = (
        f"eval:{EVAL_CONTRACT_VERSION}:{gold_set_version}:{report_date.isoformat()}"
        if eval_split == "all"
        else (
            f"eval:{EVAL_CONTRACT_VERSION}:{gold_set_version}:"
            f"{eval_split}:{report_date.isoformat()}"
        )
    )
    eval_id = _run_uuid(eval_id_seed)
    eval_id_str = str(eval_id)
    statement = pg_insert(eval_runs_table).values(
        eval_id=eval_id,
        gold_set_version=gold_set_version,
        run_id=f"eval-{gold_set_version}",
        report_date=report_date,
        metrics=_jsonable(metrics),
        thresholds=_jsonable(thresholds),
        passed=passed,
        trace_id=trace_id,
    )
    statement = statement.on_conflict_do_nothing(index_elements=["eval_id"])
    session.execute(statement)
    if commit:
        session.commit()
    return eval_id_str


def sync_gold_set_labels(session: Session, gold_set: GoldSet, *, commit: bool = True) -> int:
    """Persist gold-set labels and negative candidates with their deterministic split."""
    from ...gold_set import split_for

    rows: list[dict[str, Any]] = []
    for article in gold_set.articles:
        split = split_for(article.article_ref, gold_set.version)
        pub_date = date.fromisoformat(article.publication_date[:10])
        if not article.quotes:
            label_id = _run_uuid(f"gold-art:{gold_set.version}:{article.article_ref}")
            rows.append(
                {
                    "label_id": label_id,
                    "gold_set_version": gold_set.version,
                    "split": split,
                    "article_ref": article.article_ref,
                    "source_id": article.source_id,
                    "publication_date": pub_date,
                    "relevant": article.relevant,
                    "quote_text": None,
                    "section_id": None,
                    "human_label": False,
                    "is_negative_candidate": False,
                }
            )
        else:
            for idx, quote in enumerate(article.quotes):
                label_id = _run_uuid(f"gold-quote:{gold_set.version}:{article.article_ref}:{idx}")
                rows.append(
                    {
                        "label_id": label_id,
                        "gold_set_version": gold_set.version,
                        "split": split,
                        "article_ref": article.article_ref,
                        "source_id": article.source_id,
                        "publication_date": pub_date,
                        "relevant": article.relevant,
                        "quote_text": quote.quote_text,
                        "section_id": quote.section_id,
                        "human_label": quote.human_label,
                        "is_negative_candidate": False,
                    }
                )
    for idx, neg in enumerate(gold_set.negative_candidates):
        label_id = _run_uuid(f"gold-neg:{gold_set.version}:{idx}")
        rows.append(
            {
                "label_id": label_id,
                "gold_set_version": gold_set.version,
                "split": "test",
                "article_ref": "negative_candidate",
                "source_id": "negative",
                "publication_date": date(2026, 11, 19),
                "relevant": False,
                "quote_text": neg.quote_text,
                "section_id": None,
                "human_label": False,
                "is_negative_candidate": True,
            }
        )
    if not rows:
        return 0
    statement = pg_insert(gold_set_labels_table).values(rows)
    statement = statement.on_conflict_do_nothing(index_elements=["label_id"])
    result = session.execute(statement)
    if commit:
        session.commit()
    return _inserted_count(result, len(rows))


def eval_recent_failures(session: Session, gold_set_version: str, *, limit: int = 2) -> int:
    """Count consecutive failed eval runs for one gold set, newest first."""
    if limit < 1:
        raise ValueError("limit must be positive")
    rows = (
        session.execute(
            select(eval_runs_table.c.passed)
            .where(eval_runs_table.c.gold_set_version == gold_set_version)
            .order_by(eval_runs_table.c.report_date.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    failures = 0
    for passed in rows:
        if passed:
            break
        failures += 1
    return failures


class DecisionCacheEntry(BaseModel):
    """Immutable result of one cached LLM decision (DEC-05/B2)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    cache_key: str = Field(min_length=1)
    model_snapshot: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    schema_hash: str = Field(min_length=1)
    input_hash: str = Field(min_length=1)
    output: dict[str, Any]
    trace_id: str | None = None


def upsert_decision_cache(
    session: Session, entry: DecisionCacheEntry, *, commit: bool = True
) -> bool:
    """Idempotent insert: an existing cache key is never overwritten (returns False)."""
    values = entry.model_dump(mode="json")
    statement = pg_insert(decision_cache_table).values(values)
    statement = statement.on_conflict_do_nothing(index_elements=["cache_key"])
    result = session.execute(statement)
    if commit:
        session.commit()
    rowcount = getattr(result, "rowcount", 0) or 0
    return rowcount > 0


def get_decision_cache(session: Session, cache_key: str) -> dict[str, Any] | None:
    """Look up an existing decision by unique cache key."""
    statement = select(decision_cache_table).where(decision_cache_table.c.cache_key == cache_key)
    row = session.execute(statement).mappings().first()
    return dict(row) if row else None
