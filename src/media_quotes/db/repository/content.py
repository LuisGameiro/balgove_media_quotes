"""Article, quote, classification, digest and export-record persistence."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ...config import DEFAULT_TOPIC_ID
from ...text_utils import normalise_text
from ..tables import (
    articles_table,
    digests_table,
    obsolescence_flags_table,
    quote_classifications_table,
    quotes_table,
    sources_table,
)
from .common import _inserted_count, _jsonable, _run_uuid

if TYPE_CHECKING:
    from ...models import ExportQuoteRecord


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
                        "content_hash": record.content_hash,
                        "quote_html": record.quote_html,
                        "notes": record.notes,
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
        existing_articles = set(
            session.execute(
                select(articles_table.c.article_id).where(
                    articles_table.c.article_id.in_(list(article_values))
                )
            )
            .scalars()
            .all()
        )
        inserted_articles = len(set(article_values) - existing_articles)
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
        session.execute(statement)
    inserted_quotes = 0
    if quote_values:
        # Reuse the stored display id when the same quote (business key) already exists:
        # an idempotent re-run must not treat its own row as a collision.
        # Only the date prefixes in this batch can collide with a remapped candidate.
        prefixes = sorted({str(item["canonical_quote_id"])[:10] for item in quote_values})
        existing_statement = select(
            quotes_table.c.canonical_quote_id,
            quotes_table.c.article_id,
            quotes_table.c.quote_text_normalised,
            quotes_table.c.char_start,
            quotes_table.c.char_end,
        ).where(
            or_(*(quotes_table.c.canonical_quote_id.like(f"{prefix}-%") for prefix in prefixes))
        )
        existing_by_key = {
            (str(row[1]), str(row[2] or ""), int(row[3]), int(row[4])): str(row[0])
            for row in session.execute(existing_statement)
        }
        assigned_ids = set(existing_by_key.values())
        for item in quote_values:
            key = (
                str(item["article_id"]),
                str(item["quote_text_normalised"] or ""),
                int(item["char_start"]),
                int(item["char_end"]),
            )
            stable_id = existing_by_key.get(key)
            if stable_id is not None:
                item["canonical_quote_id"] = stable_id
                continue
            quote_id = str(item["canonical_quote_id"])
            if quote_id not in assigned_ids:
                assigned_ids.add(quote_id)
                continue
            date_prefix, sequence = quote_id.rsplit("-", 1)
            next_sequence = (int(sequence) + 1) % 10000
            attempts = 0
            while f"{date_prefix}-{next_sequence:04d}" in assigned_ids:
                next_sequence = (next_sequence + 1) % 10000
                attempts += 1
                if attempts > 10000:
                    raise RuntimeError(f"Exhausted quote id namespace for prefix {date_prefix}")
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


def read_export_records(
    session: Session,
    *,
    report_date: date,
    window_days: int,
    topic_id: str | None = None,
) -> list[Any]:
    """Rebuild strict export records from persisted quotes for one report window."""
    from datetime import timedelta

    from ...models import ExportQuoteRecord, VerificationRecord

    window_start = report_date - timedelta(days=window_days - 1)
    active_topic = topic_id or DEFAULT_TOPIC_ID
    statement = (
        select(
            quotes_table.c.canonical_quote_id.label("q_id"),
            quotes_table.c.quote_text_raw.label("q_text"),
            quotes_table.c.quote_text_en.label("q_text_en"),
            quotes_table.c.speaker.label("q_speaker"),
            quotes_table.c.char_start.label("q_char_start"),
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
            obsolescence_flags_table.c.is_obsolete.label("o_obsolete"),
            obsolescence_flags_table.c.reason.label("o_reason"),
            obsolescence_flags_table.c.created_at.label("o_created_at"),
        )
        .select_from(
            quotes_table.join(
                articles_table, quotes_table.c.article_id == articles_table.c.article_id
            )
            .join(sources_table, articles_table.c.source_id == sources_table.c.source_id)
            .outerjoin(
                obsolescence_flags_table,
                quotes_table.c.quote_id == obsolescence_flags_table.c.quote_id,
            )
        )
        .where(
            quotes_table.c.topic_id == active_topic,
            articles_table.c.publication_date >= window_start,
            articles_table.c.publication_date <= report_date,
            quotes_table.c.verification_status == "verified",
        )
        .order_by(articles_table.c.publication_date, quotes_table.c.canonical_quote_id)
    )
    # A quote can carry several flags (one per review decision); keep the newest one.
    unique_rows: dict[Any, Any] = {}
    for row in session.execute(statement).mappings():
        previous = unique_rows.get(row["q_id"])
        if previous is None or (row.get("o_created_at") or datetime.min.replace(tzinfo=UTC)) > (
            previous.get("o_created_at") or datetime.min.replace(tzinfo=UTC)
        ):
            unique_rows[row["q_id"]] = row
    records: list[Any] = []
    for row in unique_rows.values():
        meta = row["q_meta"] or {}
        article_meta = row["a_meta"] or {}
        details = row["q_details"] or {}
        stored_verified_at = details.get("verified_at")
        if isinstance(stored_verified_at, str):
            verified_at = datetime.fromisoformat(stored_verified_at.replace("Z", "+00:00"))
        elif isinstance(stored_verified_at, datetime):
            verified_at = stored_verified_at
        else:
            verified_at = datetime.combine(row["a_date"], time(12, 0), tzinfo=UTC)
        verification = VerificationRecord(
            verbatim_match=bool(details.get("verbatim_match", False)),
            metadata_verified=bool(details.get("metadata_verified", False)),
            method=str(details.get("method") or row["q_method"] or "db-export"),
            verified_at=verified_at,
        )
        records.append(
            ExportQuoteRecord.model_validate(
                {
                    "quote_id": row["q_id"],
                    "quote_text_original": row["q_text"],
                    "quote_html": str(meta.get("quote_html") or ""),
                    "quote_text_en": row["q_text_en"] or "",
                    "speaker": row["q_speaker"] or "",
                    "source_id": row["s_id"],
                    "source_name": row["s_name"],
                    "publication_name": str(article_meta.get("publication_name") or ""),
                    "source_tier": row["s_tier"],
                    "source_country_region": row["s_region"],
                    "source_type": row["s_type"],
                    "rights_status": row["s_rights"],
                    "access_mode": row["s_access"],
                    "license_reference": row["s_license"] or "",
                    "discovery_method": row["s_discovery"],
                    "claim_only": row["s_claim"],
                    "original_reporting_flag": meta.get("original_reporting_flag") or "unknown",
                    "source_language": row["a_language"] or row["s_language"],
                    "publication_date": row["a_date"].isoformat(),
                    "article_name": row["a_title"] or "",
                    "article_author": str(article_meta.get("byline") or ""),
                    "article_url": row["a_url"],
                    "extraction_date": str(
                        article_meta.get("extraction_date") or row["a_date"].isoformat()
                    ),
                    "section_id": meta.get("section_id") or "other",
                    "is_obsolete": bool(row.get("o_obsolete") or False),
                    "obsolete_reason": str(row.get("o_reason") or ""),
                    "content_hash": (
                        str(meta.get("content_hash") or "")
                        if len(str(meta.get("content_hash") or "")) == 64
                        else str((row["q_details"] or {}).get("content_hash") or "")
                        if len(str((row["q_details"] or {}).get("content_hash") or "")) == 64
                        else row["a_hash"]
                    ),
                    "verification": verification.model_dump(mode="json"),
                    "notes": str(meta.get("notes") or ""),
                    "topic_id": row["q_topic"] or active_topic,
                    "topic_status": row["q_topic_status"] or "accepted",
                    "topic_rule_version": row["q_rule_version"] or "",
                    "topic_score": row["q_score"] or 0.0,
                    "matched_terms": list(row["q_terms"] or []),
                    "quote_language": row["q_language"] or "",
                    "language_confidence": row["q_lang_conf"] or 0.0,
                    "translation_model_snapshot": row["q_t_model"] or "",
                    "translation_prompt_version": row["q_t_prompt"] or "",
                    "translation_hash": row["q_t_hash"] or "",
                    "translation_status": row["q_t_status"] or "",
                }
            )
        )
    return records
