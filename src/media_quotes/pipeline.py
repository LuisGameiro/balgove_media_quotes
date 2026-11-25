"""Public daily pipeline service shared by the CLI and Dagster.

The service owns T0-gated live collection, cached-blob ingest, explicit fixture replay,
deterministic verification, dedupe, file artifacts, audit reporting and optional Postgres
persistence. Callers supply only the settings, date and their own session context when they
want dependency injection.
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from functools import cache
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from .config import DEFAULT_TOPIC_ID, Settings
from .db.resources import SessionContextFactory
from .exceptions import ConfigError, MediaQuotesError
from .models import ExportQuoteRecord
from .risk_controls.models import ObsolescenceFlag
from .topics import TopicPack


class SourceTopicRoute(Protocol):
    """Shape returned by source_topics.load_source_topic_routes()."""

    source_id: str
    route_id: str
    approved: bool
    translation_rights_confirmed: bool
    topic_rule_id: str
    license_reference: str


class TopicDecision(Protocol):
    """Shape returned by relevance.evaluate_topic()."""

    status: str
    score: float
    matched_terms: Sequence[str]
    rule_id: str
    rule_version: str
    reasons: Sequence[str]


class LanguageDecision(Protocol):
    """Shape returned by language.detect_language()."""

    language: str
    confidence: float
    evidence: object


class TranslationResult(Protocol):
    """Shape returned by translation.translate_quote()."""

    text: str
    source_language: str
    status: str
    model_snapshot: str
    prompt_version: str
    content_hash: str
    error: str | None


# Judge providers may be a callable or expose judge()/decide(); ask_judge() adapts both.
JudgeProvider = Any


@dataclass(frozen=True, slots=True)
class TopicDecisionSnapshot:
    status: str
    score: float
    matched_terms: tuple[str, ...]
    rule_id: str
    rule_version: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LanguageDecisionSnapshot:
    language: str
    confidence: float
    evidence: str


@dataclass(frozen=True, slots=True)
class TranslationSnapshot:
    text: str
    source_language: str
    status: str
    model_snapshot: str
    prompt_version: str
    content_hash: str
    error: str
    usable: bool


@dataclass(frozen=True, slots=True)
class JudgeDecisionSnapshot:
    task: str
    verdict: str
    confidence: float
    rationale: str
    actor: str
    model_snapshot: str
    prompt_version: str
    input_hash: str
    output_hash: str
    escalation_reason: str


@dataclass(frozen=True, slots=True)
class TopicAssessment:
    status: Literal["accepted", "uncertain", "rejected"]
    score: float
    matched_terms: tuple[str, ...]
    rule_id: str
    rule_version: str
    reasons: tuple[str, ...]
    escalated: bool = False
    judge: JudgeDecisionSnapshot | None = None


def _field(value: Any, name: str, default: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    result = getattr(value, name, default)
    return default if result is None else result


def _text(value: Any) -> str:
    return value if isinstance(value, str) else str(value) if value is not None else ""


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Sequence):
        return tuple(_text(item) for item in value if _text(item))
    return ()


def load_source_topic_routes(path: Path) -> dict[str, list[SourceTopicRoute]]:
    from .source_topics import load_source_topic_routes

    return cast(dict[str, list[SourceTopicRoute]], load_source_topic_routes(path))


def load_topic_rules(path: Path) -> dict[str, dict[str, Any]]:
    from .relevance import load_topic_rules

    return load_topic_rules(path)


def evaluate_topic(rule: Mapping[str, Any], article: Mapping[str, Any]) -> Any:
    from .relevance import evaluate_topic

    return evaluate_topic(dict(rule), dict(article))


def detect_language(text: str) -> Any:
    from .language import detect_language

    return detect_language(text)


def translate_quote(
    text: str,
    *,
    source_language: str,
    settings: Settings,
    provider: Any = None,
) -> Any:
    from .translation import translate_quote

    return translate_quote(
        text, source_language=source_language, settings=settings, provider=provider
    )


def make_judge(settings: Settings) -> JudgeProvider:
    from .judge import make_judge

    return make_judge(settings)


def normalise_topic_decision(value: Any) -> TopicDecisionSnapshot:
    return TopicDecisionSnapshot(
        status=_text(_field(value, "status", "rejected")),
        score=_float(_field(value, "score", 0.0)),
        matched_terms=_strings(_field(value, "matched_terms", ())),
        rule_id=_text(_field(value, "rule_id", "")),
        rule_version=_text(_field(value, "rule_version", "")),
        reasons=_strings(_field(value, "reasons", ())),
    )


def normalise_language_decision(value: Any) -> LanguageDecisionSnapshot:
    return LanguageDecisionSnapshot(
        language=_text(_field(value, "language", "unknown")),
        confidence=_float(_field(value, "confidence", 0.0)),
        evidence=_text(_field(value, "evidence", "")),
    )


def normalise_translation_result(value: Any) -> TranslationSnapshot:
    status = _text(_field(value, "status", "failed"))
    text = _text(_field(value, "text", ""))
    error = _text(_field(value, "error", ""))
    return TranslationSnapshot(
        text=text,
        source_language=_text(_field(value, "source_language", "")),
        status=status,
        model_snapshot=_text(_field(value, "model_snapshot", "")),
        prompt_version=_text(_field(value, "prompt_version", "")),
        content_hash=_text(_field(value, "content_hash", "")),
        error=error,
        usable=bool(text) and not error and status in {"translated", "ok", "success", "passed"},
    )


def normalise_judge_decision(value: Any) -> JudgeDecisionSnapshot:
    return JudgeDecisionSnapshot(
        task=_text(_field(value, "task", "")),
        verdict=_text(_field(value, "verdict", "")).lower(),
        confidence=_float(_field(value, "confidence", 0.0)),
        rationale=_text(_field(value, "rationale", "")),
        actor=_text(_field(value, "actor", "ai-judge")),
        model_snapshot=_text(_field(value, "model_snapshot", "")),
        prompt_version=_text(_field(value, "prompt_version", "")),
        input_hash=_text(_field(value, "input_hash", "")),
        output_hash=_text(_field(value, "output_hash", "")),
        escalation_reason=_text(_field(value, "escalation_reason", "")),
    )


def ask_judge(judge: JudgeProvider, *, task: str, payload: Mapping[str, Any]) -> Any:
    """Call a judge provider without depending on its exact call signature."""
    methods = [getattr(judge, name, None) for name in ("judge", "decide", "evaluate")]
    methods.append(judge if callable(judge) else None)
    errors: list[Exception] = []
    for method in methods:
        if not callable(method):
            continue
        try:
            return _call_judge_method(method, task=task, payload=payload)
        except TypeError as exc:
            errors.append(exc)
    if errors:
        raise errors[-1]
    raise TypeError("judge provider exposes no callable entry point")


def _call_judge_method(method: Callable[..., Any], *, task: str, payload: Mapping[str, Any]) -> Any:
    parameters: Mapping[str, inspect.Parameter]
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        parameters = {}
    if parameters:
        names = set(parameters)
        accepts_kwargs = any(
            param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters.values()
        )
        accepts_positional = any(
            param.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            for param in parameters.values()
        )
        if "task" in names:
            kwargs: dict[str, Any] = {"task": task}
            for key in (
                "context",
                "payload",
                "input",
                "input_data",
                "data",
                "article",
                "decision",
            ):
                if key in names or accepts_kwargs:
                    kwargs[key] = dict(payload)
                    break
            return method(**kwargs)
        if accepts_kwargs:
            return method(task=task, **dict(payload))
        if accepts_positional:
            return method(task, dict(payload))
    return method(task=task, payload=dict(payload))


def assess_topic(
    rule: Mapping[str, Any],
    article: Mapping[str, Any],
    *,
    judge: JudgeProvider | None = None,
) -> TopicAssessment:
    """Evaluate the deterministic rule; only the uncertain band may reach the judge."""
    decision = normalise_topic_decision(evaluate_topic(rule, article))
    status = (
        decision.status if decision.status in {"accepted", "uncertain", "rejected"} else "rejected"
    )
    assessment = TopicAssessment(
        status=cast(Literal["accepted", "uncertain", "rejected"], status),
        score=decision.score,
        matched_terms=decision.matched_terms,
        rule_id=decision.rule_id,
        rule_version=decision.rule_version,
        reasons=decision.reasons,
    )
    if assessment.status != "uncertain" or judge is None:
        return assessment
    judged = normalise_judge_decision(
        ask_judge(
            judge,
            task="topic_uncertain",
            payload={
                "topic_decision": {
                    "status": decision.status,
                    "score": decision.score,
                    "matched_terms": list(decision.matched_terms),
                    "rule_id": decision.rule_id,
                    "rule_version": decision.rule_version,
                    "reasons": list(decision.reasons),
                },
                "article": dict(article),
            },
        )
    )
    accepts = judged.verdict in {"accept", "accepted", "confirm", "confirmed"}
    reasons = (*assessment.reasons, judged.rationale)
    if not accepts or judged.escalation_reason or judged.task not in {"", "topic_uncertain"}:
        reasons += ("ai-judge did not clear the uncertain band",)
        return TopicAssessment(
            status="rejected",
            score=assessment.score,
            matched_terms=assessment.matched_terms,
            rule_id=assessment.rule_id,
            rule_version=assessment.rule_version,
            reasons=tuple(reason for reason in reasons if reason),
            escalated=True,
            judge=judged,
        )
    return TopicAssessment(
        status="accepted",
        score=assessment.score,
        matched_terms=assessment.matched_terms,
        rule_id=assessment.rule_id,
        rule_version=assessment.rule_version,
        reasons=tuple(reason for reason in reasons if reason),
        judge=judged,
    )


@dataclass(frozen=True, slots=True)
class DailyPipelineResult:
    """Auditable output of one daily collection run."""

    run_id: str
    report_date: date
    dry_run: bool
    records: tuple[ExportQuoteRecord, ...]
    coverage: tuple[dict[str, Any], ...]
    gaps: tuple[dict[str, Any], ...]
    events: tuple[dict[str, Any], ...]
    rejected: int
    rejected_total: int
    status: str
    topic_id: str = DEFAULT_TOPIC_ID
    review_flags: tuple[ObsolescenceFlag, ...] = ()
    offsets: dict[str, dict[str, Any]] = field(default_factory=dict)
    store_path: Path | None = None
    digest_path: Path | None = None
    logs_path: Path | None = None
    audit_json_path: Path | None = None
    audit_md_path: Path | None = None
    checks: dict[str, Any] = field(default_factory=dict)
    persistence: dict[str, Any] = field(default_factory=dict)


@cache
def _shared_session_factory(database_url: str) -> Any:
    """One engine per database URL, reused across every call in this process."""
    from .db.engine import create_db_engine_from_url, make_session_factory

    return make_session_factory(create_db_engine_from_url(database_url))


@contextmanager
def _default_session_context(settings: Settings) -> Any:
    """Yield a caller-owned session from the process-wide engine."""
    with _shared_session_factory(settings.database_url_value)() as session:
        yield session


def _persist_result(
    result: DailyPipelineResult,
    settings: Settings,
    *,
    pipeline: str,
    session_context: SessionContextFactory | None,
) -> dict[str, Any]:
    from .cli.common import _window
    from .db.repository import (
        append_audit_events,
        finish_pipeline_run,
        persist_articles_and_quotes,
        persist_digest_manifest,
        persist_obsolescence_flags,
        persist_quote_classifications,
        start_pipeline_run,
    )

    window_start, _ = _window(result.report_date, settings)
    assigned_by = "llm" if settings.llm_provider == "openrouter" else "rule"
    # Defence in depth: only topic-accepted records may ever reach quote tables.
    publishable = [record for record in result.records if record.topic_status == "accepted"]
    context_factory = session_context or _default_session_context
    try:
        with context_factory(settings) as session:
            start_pipeline_run(
                session,
                run_id=result.run_id,
                pipeline=pipeline,
                partition_date=result.report_date,
                topic_id=result.topic_id,
                commit=False,
            )
            articles_n, quotes_n = persist_articles_and_quotes(
                session, publishable, result.offsets, commit=False
            )
            classifications_n = persist_quote_classifications(
                session, publishable, result.offsets, assigned_by=assigned_by, commit=False
            )
            digest_id = persist_digest_manifest(
                session,
                digest_date=result.report_date,
                window_start=window_start,
                window_end=result.report_date,
                md_path=str(result.digest_path or ""),
                records=publishable,
                topic_id=result.topic_id,
                commit=False,
            )
            flags_n = persist_obsolescence_flags(session, list(result.review_flags), commit=False)
            append_audit_events(session, list(result.events) + list(result.coverage), commit=False)
            finish_pipeline_run(
                session,
                run_id=result.run_id,
                status=result.status,
                stats={
                    **result.checks,
                    "topic_id": result.topic_id,
                    "quotes": len(publishable),
                    "topic_gated_records": len(result.records) - len(publishable),
                    "articles": articles_n,
                    "stored_quotes": quotes_n,
                    "classifications": classifications_n,
                    "obsolescence_flags": flags_n,
                    "digest_id": digest_id,
                },
                commit=False,
            )
            session.commit()
    except Exception as exc:
        raise MediaQuotesError(
            f"Failed to persist pipeline results to PostgreSQL: {exc}. "
            "Check the database host or set PERSIST_TO_POSTGRES=false "
            "to run in offline/file-backed mode."
        ) from exc
    return {
        "articles": articles_n,
        "stored_quotes": quotes_n,
        "classifications": classifications_n,
        "obsolescence_flags": flags_n,
        "digest_id": digest_id,
    }


def _fetch_live_blobs(
    settings: Settings,
    *,
    run_id: str,
    report_date: date,
    topic_pack: TopicPack | None,
    topic_id: str,
    tier: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch approved source feeds into the blob cache without any fixture fallback."""
    from .adapters import BlobCache
    from .cli.common import (
        _LIVE_ARTICLES_PER_SOURCE,
        _event,
        _gap_record,
        _load_registry,
        _load_source_configs,
    )
    from .collection import collect_feed
    from .fetch import fetch_policy_from

    rows = [row for row in _load_registry(settings) if tier is None or row.tier == tier]
    configs = _load_source_configs(settings)
    cache = BlobCache(settings.blob_dir)
    policy = fetch_policy_from(settings).model_copy(update={"follow_redirects": True})
    start = report_date - timedelta(days=settings.report_window_days - 1)
    gaps: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    for row in rows:
        if not row.usable or row.tier_block:
            continue
        config = configs.get(row.source_id)
        feed_url = config.discovery_feed_url(topic_id) if config is not None else ""
        feed_url = feed_url or row.feed_url
        if not feed_url:
            detail = "no approved feed or sitemap URL is configured"
            gaps.append(_gap_record(row, detail))
            events.append(
                _event(
                    run_id=run_id,
                    trace_id=f"trace-live-{row.source_id}",
                    source_id=row.source_id,
                    step="fetch",
                    status="error",
                    error=detail,
                    license_reference=row.license_reference or "(none)",
                )
            )
            continue
        try:
            result = collect_feed(
                row,
                feed_url,
                policy=policy,
                topic_pack=topic_pack,
                require_topic=True,
                limit=_LIVE_ARTICLES_PER_SOURCE,
                start=start,
                end=report_date,
                extra_hosts=frozenset(config.redirect_hosts) if config else frozenset(),
                cache=cache,
            )
        except Exception as exc:
            detail = str(exc) or type(exc).__name__
            gap = _gap_record(row, detail)
            gap["gap_reason"] = "live_fetch_failed"
            gaps.append(gap)
            events.append(
                _event(
                    run_id=run_id,
                    trace_id=f"trace-live-{row.source_id}",
                    source_id=row.source_id,
                    step="fetch",
                    status="error",
                    error=detail,
                    license_reference=row.license_reference or "(none)",
                )
            )
            continue

        errors = [article.error for article in result.articles if article.error]
        downloaded = sum(not article.error for article in result.articles)
        events.append(
            _event(
                run_id=run_id,
                trace_id=f"trace-live-{row.source_id}",
                source_id=row.source_id,
                step="fetch",
                status="error" if errors or not result.articles else "ok",
                feed_url=feed_url,
                articles_discovered=len(result.discovered),
                articles_fetched=downloaded,
                error=errors[0] if errors else "",
                license_reference=row.license_reference or "(none)",
            )
        )
        if errors or not result.articles:
            detail = errors[0] if errors else "no articles discovered in the report window"
            gap = _gap_record(row, detail)
            gap["gap_reason"] = "live_fetch_failed"
            gaps.append(gap)

    return gaps, events


def run_daily_pipeline(
    settings: Settings,
    *,
    report_date: date,
    run_id: str | None = None,
    dry_run: bool = False,
    pipeline: str = "mq-run",
    topic_id: str | None = None,
    persist_to_postgres: bool | None = None,
    session_context: SessionContextFactory | None = None,
    source_mode: str = "live",
    tier: int | None = None,
) -> DailyPipelineResult:
    """Run one report date through live, cached-blob or explicit fixture collection."""
    from .cli.audit import _audit_report, _write_audit
    from .cli.common import (
        _NOTICE,
        _digest_path,
        _load_topic_pack,
        _run_id,
        _version_manifest,
        _window,
        _write_json,
    )
    from .cli.run import _collect, _load_review_flags
    from .curation import dedupe_quotes, review_badge
    from .digest import write_digest
    from .export import atomic_write_text, local_store_path, write_jsonl
    from .llm import make_provider
    from .rights_guard import apply_rights_limits

    if source_mode not in {"live", "blobs", "fixtures"}:
        raise ConfigError("source_mode must be live, blobs or fixtures")

    settings = settings.for_source_mode(source_mode)
    active_topic_id = str(
        topic_id or getattr(settings, "active_topic", DEFAULT_TOPIC_ID) or DEFAULT_TOPIC_ID
    )
    topic_pack = _load_topic_pack(settings, active_topic_id, strict=topic_id is not None)
    actual_run_id = run_id or _run_id(report_date, settings, topic_id=active_topic_id)

    if dry_run and source_mode == "live":
        from .cli.common import _load_registry

        coverage = [
            {"source_id": row.source_id, "tier": row.tier, "status": "planned"}
            for row in _load_registry(settings)
            if (tier is None or row.tier == tier) and row.usable and not row.tier_block
        ]
        return DailyPipelineResult(
            run_id=actual_run_id,
            report_date=report_date,
            dry_run=True,
            records=(),
            coverage=tuple(coverage),
            gaps=(),
            events=(),
            rejected=0,
            rejected_total=0,
            status="dry-run",
            topic_id=active_topic_id,
            offsets={},
        )

    live_gaps: list[dict[str, Any]] = []
    live_events: list[dict[str, Any]] = []
    collection_mode = source_mode
    if source_mode == "live":
        live_gaps, live_events = _fetch_live_blobs(
            settings,
            run_id=actual_run_id,
            report_date=report_date,
            topic_pack=topic_pack,
            topic_id=active_topic_id,
            tier=tier,
        )
        collection_mode = "blobs"

    records, coverage, gaps, events, rejected, offsets = _collect(
        settings,
        run_id=actual_run_id,
        report_date=report_date,
        topic_pack=topic_pack,
        source_mode=collection_mode,
        tier=tier,
    )
    # Live prefetch and blob collection can report the same source twice; keep one
    # gap per (source, reason) so the audit does not double-count it.
    merged_gaps = live_gaps + gaps
    seen_gap_keys: set[tuple[str, str]] = set()
    deduped_gaps: list[dict[str, Any]] = []
    for gap in merged_gaps:
        key = (str(gap.get("source_id") or ""), str(gap.get("gap_reason") or ""))
        if key in seen_gap_keys:
            continue
        seen_gap_keys.add(key)
        deduped_gaps.append(gap)
    gaps = deduped_gaps
    events = live_events + events
    publishable_records = [record for record in records if record.topic_status == "accepted"]
    rejected += len(records) - len(publishable_records)
    records = publishable_records

    if dry_run:
        return DailyPipelineResult(
            run_id=actual_run_id,
            report_date=report_date,
            dry_run=True,
            records=tuple(records),
            coverage=tuple(coverage),
            gaps=tuple(gaps),
            events=tuple(events),
            rejected=rejected,
            rejected_total=rejected,
            status="dry-run",
            topic_id=active_topic_id,
            offsets=offsets,
        )

    rights = apply_rights_limits(records, max_quote_words=settings.max_quote_words)
    kept = dedupe_quotes(rights.kept, window_days=settings.report_window_days)
    store_path = local_store_path(settings.export_dir, report_date, topic_id=active_topic_id)
    write_jsonl(kept, store_path)

    now = datetime.combine(report_date, time(12, 0), tzinfo=UTC)
    review_flags = _load_review_flags(settings)

    # DEC-17 / FR-2.4: propose new obsolescence candidates using the live judge.
    # Offline runs produce no new flags (judge confidence below threshold) which is safe.
    # New proposals are merged with existing flags; duplicates by old_quote_id are dropped.
    try:
        from .risk_controls.obsolescence import propose_obsolescence as _propose_obsolescence

        _judge = make_judge(settings)
        # Historical replay must never spend on paid judge calls.
        _proposal_kwargs: dict[str, Any] = (
            {"max_decisions": 0} if pipeline in {"mq-backfill", "dagster-backfill"} else {}
        )
        _new_flags = _propose_obsolescence(list(kept), now=now, judge=_judge, **_proposal_kwargs)
        _existing_ids = {f.evidence.old_quote_id for f in review_flags}
        _merged = list(review_flags) + [
            f for f in _new_flags if f.evidence.old_quote_id not in _existing_ids
        ]
        if _merged != list(review_flags):
            _queue_path = settings.audit_dir / "review-queue.json"
            _write_json(_queue_path, {"flags": [f.model_dump(mode="json") for f in _merged]})
            review_flags = _merged
    except Exception as exc:
        # Offline / no-LLM mode must not block the pipeline, but failures are not silent.
        logging.getLogger(__name__).warning("obsolescence proposal failed: %s", exc)

    review_badges: dict[str, str] = {}
    for flag in review_flags:
        badge = review_badge(flag, now=now)
        if badge:
            review_badges[flag.evidence.old_quote_id] = badge

    provider = make_provider(settings, topic_pack=topic_pack)
    provenance = {
        **_version_manifest(
            settings, topic_id=active_topic_id, fixture_replay=source_mode == "fixtures"
        ),
        "run_id": actual_run_id,
        "source_mode": source_mode,
        "provider_label": settings.llm_provider,
    }
    digest_path = write_digest(
        kept,
        report_date=report_date,
        window_start=_window(report_date, settings)[0],
        window_end=report_date,
        out_path=_digest_path(settings, report_date, topic_id=active_topic_id),
        sections_csv=settings.sections_csv,
        topic_pack=topic_pack,
        provider=provider,
        obsolescence_flags=review_flags,
        review_badges=review_badges,
        notice=_NOTICE if source_mode == "fixtures" else "",
        provenance=provenance,
        gaps=gaps,
        now=now,
    )

    logs_path = settings.logs_dir / f"{actual_run_id}.jsonl"
    atomic_write_text(
        logs_path,
        "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for event in events
        ),
    )

    # Keep fixture runs strictly file-backed even when the caller explicitly asks to
    # persist, and let the audit report describe what actually happened.
    should_persist = source_mode != "fixtures" and (
        settings.persist_to_postgres if persist_to_postgres is None else persist_to_postgres
    )
    report = _audit_report(
        settings,
        report_date=report_date,
        run_id=actual_run_id,
        records=kept,
        coverage=coverage,
        gaps=gaps,
        events=events,
        rejected=rejected + len(rights.rejected),
        review_queue=[flag.model_dump(mode="json") for flag in review_flags],
        topic_id=active_topic_id,
        source_mode=source_mode,
        should_persist=should_persist,
    )
    audit_json_path, audit_md_path = _write_audit(settings, report)
    _write_json(settings.audit_dir / f"{actual_run_id}.json", report)

    status = "partial" if gaps else "succeeded" if report["checks"].get("gates_pass") else "failed"
    result = DailyPipelineResult(
        run_id=actual_run_id,
        report_date=report_date,
        dry_run=False,
        records=tuple(kept),
        coverage=tuple(coverage),
        gaps=tuple(gaps),
        events=tuple(events),
        rejected=rejected,
        rejected_total=rejected + len(rights.rejected),
        status=status,
        topic_id=active_topic_id,
        review_flags=tuple(review_flags),
        offsets=offsets,
        store_path=store_path,
        digest_path=digest_path,
        logs_path=logs_path,
        audit_json_path=audit_json_path,
        audit_md_path=audit_md_path,
        checks=dict(report["checks"]),
    )

    if should_persist:
        result.persistence.update(
            _persist_result(result, settings, pipeline=pipeline, session_context=session_context)
        )
    return result
