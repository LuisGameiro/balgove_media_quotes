"""Idempotent database writes for the source registry."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .tables import (
    articles_table,
    audit_events_table,
    decision_cache_table,
    eval_runs_table,
    gold_set_labels_table,
    pipeline_runs_table,
    quotes_table,
    sources_table,
)

if TYPE_CHECKING:
    from ..gold_set import GoldSet
    from ..models import ExportQuoteRecord
    from ..registry import SourceRegistryRow


def record_eval_run(
    session: Session,
    *,
    gold_set_version: str,
    report_date: date,
    metrics: dict[str, Any],
    thresholds: dict[str, Any],
    passed: bool,
    trace_id: str | None = None,
) -> str:
    """Append one evaluation result; eval ids are stable per gold set + date."""
    eval_id = _run_uuid(f"eval:{gold_set_version}:{report_date.isoformat()}")
    statement = pg_insert(eval_runs_table).values(
        eval_id=eval_id,
        gold_set_version=gold_set_version,
        run_id=f"eval-{gold_set_version}",
        report_date=report_date,
        metrics=metrics,
        thresholds=thresholds,
        passed=passed,
        trace_id=trace_id,
    )
    statement = statement.on_conflict_do_nothing(index_elements=["eval_id"])
    session.execute(statement)
    session.commit()
    return eval_id


def sync_gold_set_labels(session: Session, gold_set: GoldSet) -> int:
    """Persist gold-set labels and negative candidates with their deterministic split."""
    from ..gold_set import split_for

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
    session.commit()
    return getattr(result, "rowcount", 0) or len(rows)


def persist_articles_and_quotes(
    session: Session,
    records: list[ExportQuoteRecord],
    offsets: dict[str, dict[str, Any]],
    *,
    extractor_version: str = "fixture-replay-v1",
) -> tuple[int, int]:
    """Persist one run's verified quotes idempotently; returns (articles, quotes) inserted.

    `offsets` is keyed by canonical quote id and must carry char_start/char_end plus the
    article text hash; without it the quote row is skipped (never invent offsets).
    """
    article_values: dict[str, dict[str, Any]] = {}
    quote_values: list[dict[str, Any]] = []
    for record in records:
        detail = offsets.get(record.quote_id)
        if not detail:
            continue
        article_id = _run_uuid(f"article:{record.article_url}")
        article_values[article_id] = {
            "article_id": article_id,
            "source_id": record.source_id,
            "canonical_url": record.article_url,
            "title": record.article_name,
            "publication_date": record.publication_date,
            "language": record.source_language,
            "content_hash": detail.get("content_hash", record.content_hash),
            "status": "verified",
            "extractor_version": extractor_version,
        }
        quote_values.append(
            {
                "quote_id": _run_uuid(f"quote:{record.quote_id}"),
                "canonical_quote_id": record.quote_id,
                "article_id": article_id,
                "quote_text_raw": record.quote_text_original,
                "quote_text_normalised": record.quote_text_original,
                "char_start": int(detail["char_start"]),
                "char_end": int(detail["char_end"]),
                "dom_path": detail.get("dom_path"),
                "speaker": detail.get("speaker"),
                "attribution_span": detail.get("attribution_span"),
                "extraction_method": "nfc_exact_span_v1",
                "extractor_version": extractor_version,
                "verification_status": "verified",
                "verification_details": record.verification.model_dump(),
                "quote_metadata": {
                    "source_id": record.source_id,
                    "section_id": record.section_id,
                    "claim_only": record.claim_only,
                },
            }
        )
    inserted_articles = 0
    if article_values:
        statement = pg_insert(articles_table).values(list(article_values.values()))
        statement = statement.on_conflict_do_nothing(index_elements=["article_id"])
        result = session.execute(statement)
        inserted_articles = getattr(result, "rowcount", 0) or 0
    inserted_quotes = 0
    if quote_values:
        statement = pg_insert(quotes_table).values(quote_values)
        statement = statement.on_conflict_do_nothing(index_elements=["quote_id"])
        result = session.execute(statement)
        inserted_quotes = getattr(result, "rowcount", 0) or 0
    session.commit()
    return inserted_articles, inserted_quotes


def _run_uuid(run_id: str) -> str:
    """Derive a stable UUID from the string run ids used by the CLI."""
    return str(uuid5(NAMESPACE_URL, run_id))


def start_pipeline_run(
    session: Session,
    *,
    run_id: str,
    pipeline: str,
    partition_date: date,
) -> str:
    """Insert a run row in 'running' state; idempotent on re-run."""
    values = {
        "run_id": _run_uuid(run_id),
        "pipeline": pipeline,
        "partition_date": partition_date,
        "status": "running",
        "stats": {},
    }
    statement = pg_insert(pipeline_runs_table).values(values)
    statement = statement.on_conflict_do_nothing(index_elements=["run_id"])
    session.execute(statement)
    session.commit()
    return str(values["run_id"])


def finish_pipeline_run(
    session: Session,
    *,
    run_id: str,
    status: str,
    stats: dict[str, Any],
) -> None:
    """Terminal run state (running→succeeded/failed/partial) with final stats."""
    if status not in {"succeeded", "failed", "partial"}:
        raise ValueError(f"invalid terminal run status: {status}")
    statement = pg_insert(pipeline_runs_table).values(
        run_id=_run_uuid(run_id), status=status, stats=stats
    )
    statement = statement.on_conflict_do_update(
        index_elements=["run_id"],
        set_={"status": statement.excluded.status, "stats": statement.excluded.stats},
    )
    session.execute(statement)
    session.commit()


def _event_details(event: dict[str, Any]) -> dict[str, Any]:
    reserved = {"run_id", "trace_id", "step", "entity_type", "entity_id", "status"}
    return {key: value for key, value in event.items() if key not in reserved}


def append_audit_events(session: Session, events: list[dict[str, Any]]) -> int:
    """Append audit/coverage events; never updates existing rows."""
    if not events:
        return 0
    values = []
    for event in events:
        values.append(
            {
                "run_id": _run_uuid(str(event["run_id"])) if event.get("run_id") else None,
                "trace_id": event.get("trace_id"),
                "step": str(event.get("step") or "unknown"),
                "entity_type": event.get("entity_type"),
                "entity_id": event.get("entity_id"),
                "status": str(event.get("status") or "unknown"),
                "details": event.get("details") or _event_details(event),
            }
        )
    session.execute(audit_events_table.insert().values(values))
    session.commit()
    return len(values)


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


def upsert_decision_cache(session: Session, entry: DecisionCacheEntry) -> bool:
    """Idempotent insert: an existing cache key is never overwritten (returns False)."""
    values = entry.model_dump(mode="json")
    statement = pg_insert(decision_cache_table).values(values)
    statement = statement.on_conflict_do_nothing(index_elements=["cache_key"])
    result = session.execute(statement)
    session.commit()
    rowcount = getattr(result, "rowcount", 0) or 0
    return rowcount > 0


def get_decision_cache(session: Session, cache_key: str) -> dict[str, Any] | None:
    """Look up an existing decision by unique cache key."""
    statement = select(decision_cache_table).where(decision_cache_table.c.cache_key == cache_key)
    row = session.execute(statement).mappings().first()
    return dict(row) if row else None


def sync_sources(session: Session, rows: list[SourceRegistryRow]) -> int:
    """Upsert registry rows into Postgres. No network access."""
    if not rows:
        return 0

    values = [row.model_dump(mode="python") for row in rows]
    # Single column definition lives in tables.py; keep upserts in sync with it.
    columns = [c.name for c in sources_table.columns if c.name not in ("created_at", "updated_at")]
    statement = pg_insert(sources_table).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=["source_id"],
        set_={name: getattr(statement.excluded, name) for name in columns},
    )
    session.execute(statement)
    session.commit()
    return len(values)
