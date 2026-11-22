"""Idempotent database writes for the source registry, articles, quotes, and governance tables."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..config import DEFAULT_TOPIC_ID
from ..text_utils import normalise_text
from .tables import (
    articles_table,
    audit_events_table,
    decision_cache_table,
    digests_table,
    eval_runs_table,
    gold_set_labels_table,
    obsolescence_flags_table,
    pipeline_runs_table,
    quote_classifications_table,
    quotes_table,
    sources_table,
    storage_partitions_table,
)

if TYPE_CHECKING:
    from ..gold_set import GoldSet
    from ..models import ExportQuoteRecord
    from ..registry import SourceRegistryRow
    from ..risk_controls.models import ObsolescenceFlag


def _jsonable(value: Any) -> Any:
    """Convert dates, UUIDs and models into JSONB-safe Python values."""
    return json.loads(json.dumps(value, default=str))


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
    from ..evaluation import EVAL_CONTRACT_VERSION

    eval_id = _run_uuid(
        f"eval:{EVAL_CONTRACT_VERSION}:{gold_set_version}:{report_date.isoformat()}"
    )
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
    if commit:
        session.commit()
    return _inserted_count(result, len(rows))


def _inserted_count(result: object, attempted: int) -> int:
    """psycopg reports -1 for INSERT .. ON CONFLICT DO NOTHING; fall back to attempted."""
    rowcount = getattr(result, "rowcount", None)
    return rowcount if isinstance(rowcount, int) and rowcount >= 0 else attempted


def persist_articles_and_quotes(
    session: Session,
    records: list[ExportQuoteRecord],
    offsets: dict[str, dict[str, Any]],
    *,
    extractor_version: str = "fixture-replay-v1",
    commit: bool = True,
) -> tuple[int, int]:
    """Persist one run's verified quotes idempotently; returns (articles, quotes) inserted.

    `offsets` is keyed by canonical quote id and must carry char_start/char_end plus the
    article text hash; without it the quote row is skipped (never invent offsets).
    """
    article_values: dict[UUID, dict[str, Any]] = {}
    quote_values: list[dict[str, Any]] = []
    seen_spans: set[tuple[UUID, str, int, int]] = set()

    for record in records:
        detail = offsets.get(record.quote_id)
        if not detail:
            continue
        article_id = _run_uuid(f"article:{record.article_url}")
        char_start = int(detail["char_start"])
        char_end = int(detail["char_end"])
        norm_text = normalise_text(record.quote_text_original)
        span_key = (article_id, norm_text, char_start, char_end)

        article_values[article_id] = {
            "article_id": article_id,
            "source_id": record.source_id,
            "canonical_url": record.article_url,
            "title": record.article_name,
            "publication_date": record.publication_date,
            "language": record.source_language,
            "raw_blob_ref": detail.get("raw_blob_ref"),
            "extracted_text": detail.get("extracted_text"),
            "extraction_method": detail.get("extraction_method") or "fixture_html_v1",
            "extractor_version": detail.get("extractor_version") or extractor_version,
            "content_hash": detail.get("content_hash", record.content_hash),
            "status": "verified",
            "article_metadata": _jsonable(
                {
                    "byline": record.article_author,
                    "publication_name": record.publication_name,
                    "source_tier": record.source_tier,
                    "rights_status": record.rights_status,
                    "access_mode": record.access_mode,
                    "license_reference": record.license_reference,
                    "discovery_method": record.discovery_method,
                    "extraction_date": record.extraction_date,
                    **(detail.get("article_metadata") or {}),
                }
            ),
        }

        if span_key in seen_spans:
            continue
        seen_spans.add(span_key)

        quote_values.append(
            {
                "quote_id": _run_uuid(f"quote:{article_id}:{char_start}:{char_end}"),
                "canonical_quote_id": record.quote_id,
                "article_id": article_id,
                "quote_text_raw": record.quote_text_original,
                "quote_text_normalised": norm_text,
                "char_start": char_start,
                "char_end": char_end,
                "dom_path": detail.get("dom_path"),
                "speaker": detail.get("speaker"),
                "attribution_span": detail.get("attribution_span"),
                "extraction_method": "nfc_exact_span_v1",
                "extractor_version": extractor_version,
                "verification_status": "verified",
                "verification_details": _jsonable(record.verification.model_dump(mode="json")),
                "quote_metadata": _jsonable(
                    {
                        "source_id": record.source_id,
                        "section_id": record.section_id,
                        "topic_id": record.topic_id,
                        "claim_only": record.claim_only,
                        "original_reporting_flag": record.original_reporting_flag,
                    }
                ),
                "topic_id": record.topic_id,
                "topic_status": record.topic_status,
                "topic_rule_version": record.topic_rule_version,
                "topic_score": record.topic_score,
                "matched_terms": _jsonable(record.matched_terms),
                "quote_language": record.quote_language,
                "language_confidence": record.language_confidence,
                "quote_text_en": record.quote_text_en,
                "translation_model_snapshot": record.translation_model_snapshot,
                "translation_prompt_version": record.translation_prompt_version,
                "translation_hash": record.translation_hash,
                "translation_status": record.translation_status,
            }
        )
    inserted_articles = 0
    if article_values:
        statement = pg_insert(articles_table).values(list(article_values.values()))
        statement = statement.on_conflict_do_update(
            index_elements=["article_id"],
            set_={
                "extracted_text": statement.excluded.extracted_text,
                "raw_blob_ref": statement.excluded.raw_blob_ref,
                "extraction_method": statement.excluded.extraction_method,
                "article_metadata": statement.excluded.article_metadata,
            },
        )
        result = session.execute(statement)
        inserted_articles = _inserted_count(result, len(article_values))
    inserted_quotes = 0
    if quote_values:
        # A quote id collision with an older run is not a duplicate business record.
        # Remap only the display id, keeping the business-key conflict target below.
        # Only the date prefixes in this batch can collide with a remapped candidate.
        prefixes = sorted({str(item["canonical_quote_id"])[:10] for item in quote_values})
        existing_statement = select(quotes_table.c.canonical_quote_id).where(
            or_(*(quotes_table.c.canonical_quote_id.like(f"{prefix}-%") for prefix in prefixes))
        )
        existing_ids = {str(row[0]) for row in session.execute(existing_statement)}
        assigned_ids = set(existing_ids)
        for item in quote_values:
            quote_id = str(item["canonical_quote_id"])
            if quote_id not in assigned_ids:
                assigned_ids.add(quote_id)
                continue
            date_prefix, sequence = quote_id.rsplit("-", 1)
            next_sequence = (int(sequence) + 1) % 10000
            while f"{date_prefix}-{next_sequence:04d}" in assigned_ids:
                next_sequence = (next_sequence + 1) % 10000
            remapped = f"{date_prefix}-{next_sequence:04d}"
            item["canonical_quote_id"] = remapped
            assigned_ids.add(remapped)
        statement = pg_insert(quotes_table).values(quote_values)
        statement = statement.on_conflict_do_nothing(
            index_elements=["article_id", "quote_text_normalised", "char_start", "char_end"]
        )
        result = session.execute(statement)
        inserted_quotes = _inserted_count(result, len(quote_values))
    if commit:
        session.commit()
    return inserted_articles, inserted_quotes


def persist_quote_classifications(
    session: Session,
    records: list[ExportQuoteRecord],
    offsets: dict[str, dict[str, Any]],
    *,
    assigned_by: str = "rule",
    commit: bool = True,
) -> int:
    """Idempotently persist section classifications for verified quotes."""
    if assigned_by not in {"llm", "human", "rule"}:
        raise ValueError(f"invalid assigned_by: {assigned_by}")

    existing_quote_ids: set[UUID] = set()
    rows: list[dict[str, Any]] = []
    seen_classifications: set[UUID] = set()

    for record in records:
        detail = offsets.get(record.quote_id)
        if not detail:
            continue
        article_id = _run_uuid(f"article:{record.article_url}")
        char_start = int(detail["char_start"])
        char_end = int(detail["char_end"])
        quote_uuid = _run_uuid(f"quote:{article_id}:{char_start}:{char_end}")
        existing_quote_ids.add(quote_uuid)

        classification_id = _run_uuid(
            f"classification:{quote_uuid}:{record.section_id}:{assigned_by}"
        )
        if classification_id in seen_classifications:
            continue
        seen_classifications.add(classification_id)
        rows.append(
            {
                "classification_id": classification_id,
                "quote_id": quote_uuid,
                "section": record.section_id,
                "rationale": f"Assigned to section '{record.section_id}' via {assigned_by}",
                "decision_id": None,
                "assigned_by": assigned_by,
            }
        )

    if not rows:
        return 0
    # Resolve which quote_ids exist; ON CONFLICT may have skipped duplicate spans.
    db_quotes = set(
        session.execute(
            select(quotes_table.c.quote_id).where(quotes_table.c.quote_id.in_(existing_quote_ids))
        )
        .scalars()
        .all()
    )
    if db_quotes:
        rows = [row for row in rows if row["quote_id"] in db_quotes]
    if not rows:
        return 0

    statement = pg_insert(quote_classifications_table).values(rows)
    statement = statement.on_conflict_do_nothing(index_elements=["classification_id"])
    result = session.execute(statement)
    if commit:
        session.commit()
    return _inserted_count(result, len(rows))


def persist_digest_manifest(
    session: Session,
    *,
    digest_date: date,
    window_start: date,
    window_end: date,
    md_path: str,
    records: list[ExportQuoteRecord],
    topic_id: str = DEFAULT_TOPIC_ID,
    commit: bool = True,
) -> str:
    """Idempotently upsert the daily Markdown digest manifest into the `digests` table."""
    digest_seed = (
        f"digest:{digest_date.isoformat()}"
        if topic_id == DEFAULT_TOPIC_ID
        else f"digest:{topic_id}:{digest_date.isoformat()}"
    )
    digest_id = _run_uuid(digest_seed)
    sections: dict[str, list[str]] = {}
    for rec in records:
        sections.setdefault(rec.section_id, []).append(rec.quote_id)
    manifest = {
        "topic_id": topic_id,
        "total_quotes": len(records),
        "quote_ids": [rec.quote_id for rec in records],
        "sections": sections,
    }
    statement = pg_insert(digests_table).values(
        digest_id=digest_id,
        digest_date=digest_date,
        window_start=window_start,
        window_end=window_end,
        md_path=md_path,
        manifest=_jsonable(manifest),
        topic_id=topic_id,
    )
    statement = statement.on_conflict_do_update(
        index_elements=["topic_id", "digest_date"],
        set_={
            "window_start": statement.excluded.window_start,
            "window_end": statement.excluded.window_end,
            "md_path": statement.excluded.md_path,
            "manifest": statement.excluded.manifest,
        },
    )
    session.execute(statement)
    if commit:
        session.commit()
    return str(digest_id)


def persist_obsolescence_flags(
    session: Session,
    flags: list[ObsolescenceFlag],
    *,
    commit: bool = True,
) -> int:
    """Sync human-in-the-loop obsolescence flags to PostgreSQL when matching quotes exist."""
    if not flags:
        return 0
    canonical_ids = {flag.evidence.old_quote_id for flag in flags}
    mapping = dict(
        session.execute(
            select(quotes_table.c.canonical_quote_id, quotes_table.c.quote_id).where(
                quotes_table.c.canonical_quote_id.in_(canonical_ids)
            )
        ).all()
    )
    rows: list[dict[str, Any]] = []
    for flag in flags:
        quote_uuid = mapping.get(flag.evidence.old_quote_id)
        if not quote_uuid:
            continue
        flag_id = _run_uuid(
            f"obs-flag:{flag.evidence.old_quote_id}:{flag.evidence.decision_id}:{flag.state}"
        )
        rows.append(
            {
                "flag_id": flag_id,
                "quote_id": quote_uuid,
                "is_obsolete": flag.state == "confirmed",
                "reason": f"[{flag.state}] {flag.reason_code or flag.evidence.rationale}",
                "superseded_by": [],
                "decision_id": None,
            }
        )
    if not rows:
        return 0
    statement = pg_insert(obsolescence_flags_table).values(rows)
    statement = statement.on_conflict_do_nothing(index_elements=["flag_id"])
    result = session.execute(statement)
    if commit:
        session.commit()
    return _inserted_count(result, len(rows))


def _run_uuid(run_id: str) -> UUID:
    """Derive a stable UUID from the string ids used by the CLI."""
    return uuid5(NAMESPACE_URL, run_id)


def start_pipeline_run(
    session: Session,
    *,
    run_id: str,
    pipeline: str,
    partition_date: date,
    topic_id: str = DEFAULT_TOPIC_ID,
    commit: bool = True,
) -> str:
    """Insert a run row in 'running' state; idempotent on re-run."""
    values = {
        "run_id": _run_uuid(run_id),
        "pipeline": pipeline,
        "partition_date": partition_date,
        "status": "running",
        "stats": {},
        "topic_id": topic_id,
    }
    statement = pg_insert(pipeline_runs_table).values(values)
    statement = statement.on_conflict_do_nothing(index_elements=["run_id"])
    session.execute(statement)
    if commit:
        session.commit()
    return str(values["run_id"])


def finish_pipeline_run(
    session: Session,
    *,
    run_id: str,
    status: str,
    stats: dict[str, Any],
    commit: bool = True,
) -> None:
    """Terminal run state (running→succeeded/failed/partial) with final stats."""
    if status not in {"succeeded", "failed", "partial"}:
        raise ValueError(f"invalid terminal run status: {status}")
    statement = (
        update(pipeline_runs_table)
        .where(pipeline_runs_table.c.run_id == _run_uuid(run_id))
        .values(status=status, stats=_jsonable(stats), finished_at=datetime.now(UTC))
    )
    session.execute(statement)
    if commit:
        session.commit()


def _event_details(event: dict[str, Any]) -> dict[str, Any]:
    reserved = {"run_id", "trace_id", "step", "entity_type", "entity_id", "status"}
    return {key: value for key, value in event.items() if key not in reserved}


def append_audit_events(
    session: Session, events: list[dict[str, Any]], *, commit: bool = True
) -> int:
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
                "details": _jsonable(event.get("details") or _event_details(event)),
            }
        )
    session.execute(audit_events_table.insert().values(values))
    if commit:
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


def read_export_records(
    session: Session,
    *,
    report_date: date,
    window_days: int,
    topic_id: str | None = None,
) -> list[Any]:
    """Rebuild strict export records from persisted quotes for one report window."""
    from datetime import timedelta

    from ..models import ExportQuoteRecord, VerificationRecord

    window_start = report_date - timedelta(days=window_days - 1)
    active_topic = topic_id or DEFAULT_TOPIC_ID
    statement = (
        select(
            quotes_table.c.canonical_quote_id.label("q_id"),
            quotes_table.c.quote_text_raw.label("q_text"),
            quotes_table.c.quote_text_en.label("q_text_en"),
            quotes_table.c.quote_metadata.label("q_meta"),
            quotes_table.c.verification_details.label("q_details"),
            quotes_table.c.extraction_method.label("q_method"),
            quotes_table.c.topic_id.label("q_topic"),
            quotes_table.c.topic_status.label("q_topic_status"),
            quotes_table.c.topic_rule_version.label("q_rule_version"),
            quotes_table.c.topic_score.label("q_score"),
            quotes_table.c.matched_terms.label("q_terms"),
            quotes_table.c.quote_language.label("q_language"),
            quotes_table.c.language_confidence.label("q_lang_conf"),
            quotes_table.c.translation_model_snapshot.label("q_t_model"),
            quotes_table.c.translation_prompt_version.label("q_t_prompt"),
            quotes_table.c.translation_hash.label("q_t_hash"),
            quotes_table.c.translation_status.label("q_t_status"),
            articles_table.c.canonical_url.label("a_url"),
            articles_table.c.title.label("a_title"),
            articles_table.c.language.label("a_language"),
            articles_table.c.publication_date.label("a_date"),
            articles_table.c.article_metadata.label("a_meta"),
            articles_table.c.content_hash.label("a_hash"),
            sources_table.c.source_id.label("s_id"),
            sources_table.c.source_name.label("s_name"),
            sources_table.c.tier.label("s_tier"),
            sources_table.c.country_region.label("s_region"),
            sources_table.c.source_type.label("s_type"),
            sources_table.c.rights_status.label("s_rights"),
            sources_table.c.access_mode.label("s_access"),
            sources_table.c.license_reference.label("s_license"),
            sources_table.c.discovery_method.label("s_discovery"),
            sources_table.c.claim_only.label("s_claim"),
            sources_table.c.language.label("s_language"),
        )
        .select_from(
            quotes_table.join(
                articles_table, quotes_table.c.article_id == articles_table.c.article_id
            ).join(sources_table, articles_table.c.source_id == sources_table.c.source_id)
        )
        .where(
            quotes_table.c.topic_id == active_topic,
            articles_table.c.publication_date >= window_start,
            articles_table.c.publication_date <= report_date,
            quotes_table.c.verification_status == "verified",
        )
        .order_by(articles_table.c.publication_date, quotes_table.c.canonical_quote_id)
    )
    records: list[Any] = []
    for row in session.execute(statement).mappings():
        q = row
        a = row
        s = row
        meta = q["q_meta"] or {}
        article_meta = a["a_meta"] or {}
        details = q["q_details"] or {}
        stored_verified_at = details.get("verified_at")
        if isinstance(stored_verified_at, str):
            verified_at = datetime.fromisoformat(stored_verified_at.replace("Z", "+00:00"))
        elif isinstance(stored_verified_at, datetime):
            verified_at = stored_verified_at
        else:
            verified_at = datetime.combine(a["a_date"], time(12, 0), tzinfo=UTC)
        verification = VerificationRecord(
            verbatim_match=bool(details.get("verbatim_match", True)),
            metadata_verified=bool(details.get("metadata_verified", True)),
            method=str(details.get("method") or q["q_method"] or "db-export"),
            verified_at=verified_at,
        )
        records.append(
            ExportQuoteRecord.model_validate(
                {
                    "quote_id": q["q_id"],
                    "quote_text_original": q["q_text"],
                    "quote_text_en": q["q_text_en"] or "",
                    "source_id": s["s_id"],
                    "source_name": s["s_name"],
                    "publication_name": str(article_meta.get("publication_name") or ""),
                    "source_tier": s["s_tier"],
                    "source_country_region": s["s_region"],
                    "source_type": s["s_type"],
                    "rights_status": s["s_rights"],
                    "access_mode": s["s_access"],
                    "license_reference": s["s_license"] or "",
                    "discovery_method": s["s_discovery"],
                    "claim_only": s["s_claim"],
                    "original_reporting_flag": meta.get("original_reporting_flag") or "first-hand",
                    "source_language": a["a_language"] or s["s_language"],
                    "publication_date": a["a_date"].isoformat(),
                    "article_name": a["a_title"] or "",
                    "article_author": str(article_meta.get("byline") or ""),
                    "article_url": a["a_url"],
                    "extraction_date": str(
                        article_meta.get("extraction_date") or a["a_date"].isoformat()
                    ),
                    "section_id": meta.get("section_id") or "other",
                    "is_obsolete": False,
                    "content_hash": a["a_hash"],
                    "verification": verification.model_dump(mode="json"),
                    "topic_id": q["q_topic"] or active_topic,
                    "topic_status": q["q_topic_status"] or "accepted",
                    "topic_rule_version": q["q_rule_version"] or "",
                    "topic_score": q["q_score"] or 0.0,
                    "matched_terms": list(q["q_terms"] or []),
                    "quote_language": q["q_language"] or "",
                    "language_confidence": q["q_lang_conf"] or 0.0,
                    "translation_model_snapshot": q["q_t_model"] or "",
                    "translation_prompt_version": q["q_t_prompt"] or "",
                    "translation_hash": q["q_t_hash"] or "",
                    "translation_status": q["q_t_status"] or "",
                }
            )
        )
    return records


def sync_storage_partitions(session: Session, *, commit: bool = True) -> int:
    """Register observed audit_event months in storage_partitions as hot.

    No DDL and no data movement: this only records which partitions exist so the
    retention planner and operator have one source of truth.
    """
    from sqlalchemy import func

    months = (
        session.execute(
            select(
                func.date_trunc("month", audit_events_table.c.created_at).label("month")
            ).distinct()
        )
        .scalars()
        .all()
    )
    if not months:
        return 0
    rows = [
        {"table_name": "audit_events", "month": month.date(), "state": "hot"}
        for month in months
        if month is not None
    ]
    statement = pg_insert(storage_partitions_table).values(rows)
    statement = statement.on_conflict_do_nothing(index_elements=["table_name", "month"])
    session.execute(statement)
    if commit:
        session.commit()
    return len(rows)


def sync_sources(session: Session, rows: list[SourceRegistryRow], *, commit: bool = True) -> int:
    """Upsert registry rows into Postgres. No network access."""
    if not rows:
        return 0

    values = [row.model_dump(mode="python") for row in rows]
    # Single column definition lives in tables.py; keep upserts in sync with it.
    columns = [c.name for c in sources_table.columns if c.name not in ("created_at", "updated_at")]
    statement = pg_insert(sources_table).values(values)
    statement = statement.on_conflict_do_update(
        index_elements=["source_id"],
        set_={
            **{name: getattr(statement.excluded, name) for name in columns},
            "updated_at": datetime.now(UTC),
        },
    )
    session.execute(statement)
    if commit:
        session.commit()
    return len(values)
