"""Offline fixture-replay pipeline and run command."""

from __future__ import annotations

import contextlib
import hashlib
import json
from datetime import UTC, date, datetime, time
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from ..adapters import FixtureSource, build_adapter
from ..config import Settings
from ..curation import dedupe_quotes, review_badge
from ..digest import write_digest
from ..export import content_hash, local_store_path, quote_record, write_jsonl
from ..llm import make_provider
from ..models import ExportQuoteRecord, QuoteCandidate
from ..registry import SourceRegistryRow
from ..rights_guard import apply_rights_limits
from ..risk_controls.models import ObsolescenceFlag
from ..text_utils import normalise_text
from ..verification_stages import verify_pipeline
from .audit import _audit_report, _write_audit
from .common import (
    _MANDATORY,
    _NOTICE,
    EXIT_GAPS,
    EXIT_OK,
    _event,
    _fixture_mode,
    _gap_for,
    _gap_record,
    _load_registry,
    _read_json,
    _run_id,
    _trace_id,
    _window,
    _write_json,
)


def _load_review_flags(settings: Settings) -> list[ObsolescenceFlag]:
    queue_path = settings.audit_dir / "review-queue.json"
    if not queue_path.is_file():
        return []
    payload = _read_json(queue_path)
    flags_data = payload.get("flags", [])
    flags: list[ObsolescenceFlag] = []
    if isinstance(flags_data, list):
        for item in flags_data:
            if isinstance(item, dict):
                with contextlib.suppress(Exception):
                    flags.append(ObsolescenceFlag.model_validate(item))
    return flags


def _process_source(  # pragma: no cover
    row: SourceRegistryRow,
    *,
    settings: Settings,
    run_id: str,
    window_start: date,
    report_date: date,
    counters: dict[str, int],
) -> tuple[
    list[ExportQuoteRecord],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
    dict[str, dict[str, Any]],
]:
    """Process one source through discovery, fixture fetch, two-stage verification and metadata."""
    replay = _fixture_mode(row, settings)
    events: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    records: list[ExportQuoteRecord] = []
    offsets: dict[str, dict[str, Any]] = {}
    rejected = 0

    if not row.usable and not replay:
        # T0 gate: never call discovery/fetch for a disabled source with no fixture.
        gaps.append(_gap_record(row, "no permitted route and no fixture available"))
        events.append(
            _event(
                run_id=run_id,
                trace_id=f"trace-source-{row.source_id}",
                source_id=row.source_id,
                step="discover",
                status="skipped",
                rights_status=str(row.rights_status),
                license_reference=row.license_reference or "(none)",
                message="T0 gate blocked the source; no contact attempted",
            )
        )
        return (
            records,
            {
                "source_id": row.source_id,
                "expected_articles": 0,
                "discovered": 0,
                "processed": 0,
                "quotes": 0,
                "last_check_at": datetime.combine(report_date, time(12, 0), tzinfo=UTC).isoformat(),
                "status": "blocked",
                "gap_reason": _gap_for(row)[1],
                "mandatory": row.source_id in _MANDATORY,
            },
            gaps,
            events,
            rejected,
            offsets,
        )

    if not row.usable:
        gaps.append(_gap_record(row, "fixture replay only; the T0 gate refuses live contact"))

    adapter = build_adapter(row, FixtureSource(settings.fixtures_dir), replay=replay)
    refs = adapter.discover(window_start, report_date)
    if replay:
        events.append(
            _event(
                run_id=run_id,
                trace_id=f"trace-source-{row.source_id}",
                source_id=row.source_id,
                step="discover",
                status="fixture_replay",
                mode="fixture_replay",
                rights_status=str(row.rights_status),
                license_reference=row.license_reference or "(none)",
                fixture_count=len(refs),
                message="blocked mandatory source replayed from synthetic fixtures",
            )
        )

    policy_flags = dict(adapter.policy_flags())
    policy_flags["original_reporting_flag"] = "first-hand"
    policy_flags["fixture_replay"] = replay
    verified_at = datetime.combine(report_date, time(12, 0), tzinfo=UTC)

    for ref in refs:
        trace_id = _trace_id(row.source_id, ref)
        raw = adapter.fetch(ref)
        metadata = adapter.parse_metadata(raw)
        text = str(metadata.get("article_text") or "")
        canonical_url = str(metadata.get("canonical_url") or "")
        article_ref_name = str(metadata.get("title") or ref)
        raw_hash = hashlib.sha256(raw).hexdigest()
        events.append(
            _event(
                run_id=run_id,
                trace_id=trace_id,
                source_id=row.source_id,
                step="fetch_parse",
                status="fixture_replay" if replay else "ok",
                mode=adapter.mode,
                content_hash=raw_hash,
                canonical_url=canonical_url,
                license_reference=row.license_reference or "(none)",
            )
        )
        spans_raw = metadata.get("quote_spans", [])
        spans = [s for s in spans_raw if isinstance(s, dict)] if isinstance(spans_raw, list) else []
        by_text = {
            normalise_text(str(span.get("quote_text") or "")): span
            for span in spans
        }
        candidates: list[QuoteCandidate] = []
        for found in adapter.extract_quotes(text):
            quote_text = str(found.get("quote_text") or "")
            span = by_text.get(normalise_text(quote_text), {})
            candidates.append(
                QuoteCandidate(
                    quote_text=quote_text,
                    article_id=uuid5(NAMESPACE_URL, ref),
                    char_start=int(found.get("char_start", 0)),
                    char_end=int(found.get("char_end", 0)),
                    dom_path=str(found.get("dom_path") or span.get("dom_path") or ""),
                    speaker=str(span.get("speaker") or found.get("speaker") or "") or None,
                    attribution_span=str(
                        span.get("attribution_span") or found.get("attribution_span") or ""
                    )
                    or None,
                )
            )

        pipeline_outcome = verify_pipeline(
            candidates,
            text=text,
            source_row=row,
            policy_flags=policy_flags,
            metadata=metadata,
            window_start=window_start,
            window_end=report_date,
            max_quote_words=settings.max_quote_words,
            now=verified_at,
        )
        rejected += sum(pipeline_outcome.prefilter.rejected.values())
        for v_res in pipeline_outcome.results:
            if v_res.status != "verified":
                rejected += 1
                events.append(
                    _event(
                        run_id=run_id,
                        trace_id=trace_id,
                        source_id=row.source_id,
                        step="verify",
                        status=v_res.status,
                        quote_id="",
                        reasons=v_res.reasons,
                        license_reference=row.license_reference or "(none)",
                    )
                )

        for candidate in pipeline_outcome.publishable:
            span = by_text.get(normalise_text(candidate.quote_text), {})
            publication_date = str(metadata.get("publication_date") or report_date.isoformat())[:10]
            index = counters.get(publication_date, 0) + 1
            counters[publication_date] = index
            quote_id = f"Q-{publication_date.replace('-', '')}-{index:04d}"
            offsets[quote_id] = {
                "char_start": candidate.char_start,
                "char_end": candidate.char_end,
                "dom_path": candidate.dom_path,
                "speaker": candidate.speaker,
                "attribution_span": candidate.attribution_span,
                "content_hash": raw_hash,
            }
            section_id = str(span.get("section_id") or "other")
            records.append(
                quote_record(
                    quote_id=quote_id,
                    quote_text_original=candidate.quote_text,
                    source_id=row.source_id,
                    source_name=row.source_name,
                    source_tier=row.tier,
                    source_country_region=row.country_region,
                    source_type=str(row.source_type),
                    rights_status=str(row.rights_status),
                    access_mode=str(row.access_mode),
                    license_reference=row.license_reference,
                    discovery_method=str(row.discovery_method),
                    claim_only=row.claim_only,
                    original_reporting_flag="first-hand",
                    source_language=row.language,
                    publication_date=publication_date,
                    article_name=article_ref_name,
                    article_author=str(metadata.get("byline") or ""),
                    article_url=canonical_url,
                    extraction_date=verified_at.isoformat(),
                    section_id=section_id,
                    is_obsolete=False,
                    content_hash=content_hash(
                        [
                            row.source_id,
                            canonical_url,
                            candidate.quote_text,
                            str(candidate.char_start),
                            raw_hash,
                        ]
                    ),
                    verification={
                        "verbatim_match": True,
                        "metadata_verified": True,
                        "method": "nfc_exact_span_v1",
                        "verified_at": verified_at.isoformat(),
                    },
                    publication_name=str(metadata.get("publication_name") or row.source_name),
                    notes="synthetic fixture replay" if replay else "offline replay",
                )
            )
            events.append(
                _event(
                    run_id=run_id,
                    trace_id=trace_id,
                    source_id=row.source_id,
                    step="verify",
                    status="verified",
                    quote_id=quote_id,
                    fixture_replay=replay,
                    license_reference=row.license_reference or "(none)",
                )
            )
    if not refs and row.usable:
        gaps.append(_gap_record(row, "no permitted articles found in the replay window"))
    coverage = {
        "source_id": row.source_id,
        "expected_articles": len(refs),
        "discovered": len(refs),
        "processed": len(refs),
        "quotes": sum(1 for record in records if record.source_id == row.source_id),
        "last_check_at": verified_at.isoformat(),
        "status": "degraded" if replay or not row.usable else "ok",
        "gap_reason": "rights_blocked" if replay else "",
        "mandatory": row.source_id in _MANDATORY,
    }
    return records, coverage, gaps, events, rejected, offsets


def _collect(  # pragma: no cover
    settings: Settings, *, run_id: str, report_date: date
) -> tuple[
    list[ExportQuoteRecord],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
    dict[str, dict[str, Any]],
]:
    window_start, _ = _window(report_date, settings)
    records: list[ExportQuoteRecord] = []
    coverage: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    rejected = 0
    counters: dict[str, int] = {}
    offsets: dict[str, dict[str, Any]] = {}
    for row in _load_registry(settings):
        found, cov, row_gaps, row_events, row_rejected, row_offsets = _process_source(
            row,
            settings=settings,
            run_id=run_id,
            window_start=window_start,
            report_date=report_date,
            counters=counters,
        )
        records.extend(found)
        coverage.append(cov)
        gaps.extend(row_gaps)
        events.extend(row_events)
        rejected += row_rejected
        offsets.update(row_offsets)
    records.sort(key=lambda item: (item.publication_date, item.quote_id))
    return records, coverage, gaps, events, rejected, offsets


def _do_run(  # pragma: no cover
    settings: Settings,
    *,
    report_date: date,
    dry_run: bool,
    run_id: str | None,
    verbose: bool,
) -> int:
    actual_run_id = run_id or _run_id(report_date, settings)
    records, coverage, gaps, events, rejected, offsets = _collect(
        settings, run_id=actual_run_id, report_date=report_date
    )
    if dry_run:
        print(
            f"Dry run {actual_run_id}: {len(records)} quote(s) would be stored, "
            f"{len(gaps)} coverage gap(s), {rejected} candidate(s) rejected; no files written."
        )
        for gap in gaps:
            print(f"  gap: {gap['source_id']} ({gap['gap_reason']}) — {gap['detail']}")
        return EXIT_OK

    rights = apply_rights_limits(records, max_quote_words=settings.max_quote_words)
    records = dedupe_quotes(rights.kept, window_days=settings.report_window_days)
    store_path = local_store_path(settings.export_dir, report_date)
    write_jsonl(records, store_path)

    review_flags = _load_review_flags(settings)
    now = datetime.combine(report_date, time(12, 0), tzinfo=UTC)
    review_badges = {
        flag.evidence.old_quote_id: review_badge(flag, now=now)
        for flag in review_flags
        if review_badge(flag, now=now)
    }

    digest_path = write_digest(
        records,
        report_date=report_date,
        window_start=_window(report_date, settings)[0],
        window_end=report_date,
        out_path=settings.digest_dir / f"{report_date.isoformat()}.md",
        sections_csv=settings.sections_csv,
        provider=make_provider("local"),
        obsolescence_flags=review_flags,
        review_badges=review_badges,
        notice=_NOTICE,
    )
    logs_path = settings.logs_dir / f"{actual_run_id}.jsonl"
    logs_path.parent.mkdir(parents=True, exist_ok=True)
    with logs_path.open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(
                json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            )
            handle.write("\n")
    report = _audit_report(
        settings,
        report_date=report_date,
        run_id=actual_run_id,
        records=records,
        coverage=coverage,
        gaps=gaps,
        events=events,
        rejected=rejected + len(rights.rejected),
        review_queue=[flag.model_dump(mode="json") for flag in review_flags],
    )
    _write_audit(settings, report)
    _write_json(settings.audit_dir / f"{actual_run_id}.json", report)
    if settings.persist_to_postgres:
        from ..db.engine import create_db_engine, make_session_factory
        from ..db.repository import (
            append_audit_events,
            finish_pipeline_run,
            persist_articles_and_quotes,
            start_pipeline_run,
        )

        engine = create_db_engine(settings)
        with make_session_factory(engine)() as session:
            start_pipeline_run(
                session,
                run_id=actual_run_id,
                pipeline="mq-run",
                partition_date=report_date,
            )
            articles_n, quotes_n = persist_articles_and_quotes(session, records, offsets)
            append_audit_events(session, events + coverage)
            finish_pipeline_run(
                session,
                run_id=actual_run_id,
                status="partial" if gaps else "succeeded",
                stats={
                    "quotes": len(records),
                    "articles": articles_n,
                    "stored_quotes": quotes_n,
                    "rejected": rejected,
                    "gaps": len(gaps),
                },
            )
        print(f"Postgres: {articles_n} article(s), {quotes_n} quote(s) persisted")
    print(
        f"Run {actual_run_id}: {len(records)} verified fixture quote(s), "
        f"{rejected} rejected, {len(gaps)} coverage gap(s)."
    )
    print(f"Digest: {digest_path}")
    print(f"Store:  {store_path}")
    print(f"Logs:   {logs_path}")
    if verbose:
        print(f"Audit:  {settings.audit_dir / f'audit-{report_date.isoformat()}.md'}")
    for gap in gaps:
        if gap.get("mandatory"):
            print(
                f"COVERAGE GAP: {gap['source_id']} is {gap['rights_status']}; "
                f"{gap['gap_reason']} (fixture replay logged, no live contact)."
            )
    return EXIT_GAPS if gaps else EXIT_OK
