"""Public daily pipeline service shared by the CLI and Dagster.

The service owns the fixture/T0-gated collection, deterministic verification, dedupe,
file artifacts, audit report and optional Postgres persistence. Callers supply only the
settings, date and their own session context when they want dependency injection.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from .ai.judge import make_judge as _make_judge_impl
from .ai.language import detect_language as _detect_language_impl
from .ai.translation import translate_quote as _translate_quote_impl
from .config import DEFAULT_TOPIC_ID, Settings
from .db.resources import SessionContextFactory
from .enums import AcquisitionProvenance
from .exceptions import ConfigError
from .governance.relevance import evaluate_topic as _evaluate_topic_impl
from .governance.relevance import load_topic_rules as _load_topic_rules_impl
from .models import ExportQuoteRecord
from .risk_controls.models import ObsolescenceFlag
from .source_topics import load_source_topic_routes as _load_source_topic_routes_impl

_LIVE_NOTICE = (
    "LIVE COLLECTION: quotes below were acquired from rights-cleared publisher feeds. "
    "Each quote records its acquisition provenance and discovery route."
)


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


# Thin wrappers keep the historical ``media_quotes.pipeline`` API while the
# implementations are imported directly from their defining modules.
def load_source_topic_routes(path: Path) -> dict[str, list[SourceTopicRoute]]:
    return cast(dict[str, list[SourceTopicRoute]], _load_source_topic_routes_impl(path))


def load_topic_rules(path: Path) -> dict[str, dict[str, Any]]:
    return _load_topic_rules_impl(path)


def evaluate_topic(rule: Mapping[str, Any], article: Mapping[str, Any]) -> Any:
    return _evaluate_topic_impl(dict(rule), dict(article))


def detect_language(text: str) -> Any:
    return _detect_language_impl(text)


def translate_quote(
    text: str,
    *,
    source_language: str,
    settings: Settings,
    provider: Any = None,
) -> Any:
    return _translate_quote_impl(
        text, source_language=source_language, settings=settings, provider=provider
    )


def _call_factory(factory: Callable[..., Any], settings: Settings, **kwargs: Any) -> Any:
    """Bind the full call up front so a factory that cannot take the context fails loudly.

    Errors raised inside the factory are deliberately not caught here.
    """
    signature = inspect.signature(factory)
    signature.bind(settings, **kwargs)
    return factory(settings, **kwargs)


def make_judge(
    settings: Settings, *, session_context: SessionContextFactory | None = None
) -> JudgeProvider:
    return _call_factory(_make_judge_impl, settings, session_context=session_context)


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
    for method in methods:
        if not callable(method):
            continue
        # Validate the provider shape with signature binding only; a TypeError raised
        # inside the provider must propagate rather than trigger another candidate.
        try:
            args, kwargs = _bound_judge_call(method, task=task, payload=payload)
        except TypeError:
            continue  # this candidate cannot accept the call shape
        return method(*args, **kwargs)
    raise TypeError("judge provider exposes no callable entry point")


def _bound_judge_call(
    method: Callable[..., Any], *, task: str, payload: Mapping[str, Any]
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Bind a judge call to the provider signature, returning (args, kwargs)."""
    try:
        signature = inspect.signature(method)
    except (TypeError, ValueError):
        return (), {"task": task, "payload": dict(payload)}
    names = set(signature.parameters)
    accepts_kwargs = any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )
    accepts_positional = any(
        param.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for param in signature.parameters.values()
    )
    if "task" in names:
        kwargs: dict[str, Any] = {"task": task}
        for key in ("context", "payload", "input", "input_data", "data", "article", "decision"):
            if key in names or accepts_kwargs:
                kwargs[key] = dict(payload)
                break
        signature.bind(**kwargs)  # shape check only
        return (), kwargs
    if accepts_kwargs:
        kwargs = {"task": task, **dict(payload)}
        signature.bind(**kwargs)
        return (), kwargs
    if accepts_positional:
        signature.bind(task, dict(payload))
        return (task, dict(payload)), {}
    kwargs = {"task": task, "payload": dict(payload)}
    signature.bind(**kwargs)
    return (), kwargs


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
    provenance: AcquisitionProvenance = "fixture"
    artifact_scope: str | None = None
    review_flags: tuple[ObsolescenceFlag, ...] = ()
    offsets: dict[str, dict[str, Any]] = field(default_factory=dict)
    store_path: Path | None = None
    digest_path: Path | None = None
    logs_path: Path | None = None
    audit_json_path: Path | None = None
    audit_md_path: Path | None = None
    checks: dict[str, Any] = field(default_factory=dict)
    persistence: dict[str, Any] = field(default_factory=dict)


# One engine per process; the URL is only ever held as a sha256 digest, not as a cache key.
_engine_lock = threading.Lock()
_shared_engine_state: dict[str, Any] = {}


def _shared_session_factory(database_url: str) -> Any:
    """Reuse one engine/factory, replacing (and disposing) it when the URL changes."""
    from .db.engine import create_db_engine_from_url, make_session_factory

    key = hashlib.sha256(database_url.encode("utf-8")).hexdigest()
    with _engine_lock:
        if _shared_engine_state.get("key") != key:
            previous_engine = _shared_engine_state.get("engine")
            if previous_engine is not None:
                previous_engine.dispose()
            engine = create_db_engine_from_url(database_url)
            _shared_engine_state.update(
                key=key, engine=engine, factory=make_session_factory(engine)
            )
        return _shared_engine_state["factory"]


@contextmanager
def default_session_context(settings: Settings) -> Any:
    """Yield a caller-owned session from the process-wide engine."""
    with _shared_session_factory(settings.database_url_value)() as session:
        yield session


_default_session_context = default_session_context  # frozen alias for legacy callers


def _record_persistence_failure(
    result: DailyPipelineResult,
    settings: Settings,
    *,
    session_context: SessionContextFactory | None,
) -> None:
    """Best-effort failure marker in a fresh short session; never masks the original error."""
    from .db.repository import finish_pipeline_run

    # Best-effort: a broken session factory must not mask the original error.
    with suppress(Exception), (session_context or default_session_context)(settings) as session:
        finish_pipeline_run(
            session,
            run_id=result.run_id,
            status="failed",
            stats={"persistence_error": True},
            commit=True,
        )


def _persist_result(
    result: DailyPipelineResult,
    settings: Settings,
    *,
    pipeline: str,
    session_context: SessionContextFactory | None,
) -> dict[str, Any]:
    from .db.repository import (
        append_audit_events,
        finish_pipeline_run,
        persist_articles_and_quotes,
        persist_digest_manifest,
        persist_obsolescence_flags,
        persist_quote_classifications,
        start_pipeline_run,
    )
    from .processing.window import window

    window_start, _ = window(result.report_date, settings)
    assigned_by = "llm" if settings.llm_provider == "openrouter" else "rule"
    # Defence in depth: only topic-accepted records may ever reach quote tables.
    publishable = [record for record in result.records if record.topic_status == "accepted"]
    context_factory = session_context or default_session_context
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
            digest_id = ""
            if result.digest_path is not None and result.artifact_scope is None:
                digest_id = persist_digest_manifest(
                    session,
                    digest_date=result.report_date,
                    window_start=window_start,
                    window_end=result.report_date,
                    md_path=str(result.digest_path),
                    records=publishable,
                    topic_id=result.topic_id,
                    provenance=result.provenance,
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
        _record_persistence_failure(result, settings, session_context=session_context)
        raise RuntimeError(
            f"Failed to persist pipeline results to PostgreSQL: "
            f"{type(exc).__name__}: {exc}. "
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
    source_mode: str = "fixtures",
    tier: int | None = None,
    provenance: AcquisitionProvenance = "fixture",
    prefetched_refs: Mapping[str, list[str]] | None = None,
    source_ids: set[str] | None = None,
) -> DailyPipelineResult:
    """Run one report date through the shared T0-gated offline pipeline."""
    if source_ids is not None and not source_ids:
        raise ConfigError("source_ids cannot be empty; pass None to select all sources")
    if source_mode in {"live", "blobs", "archive"} and provenance == "fixture":
        provenance = "live_feed"
    from .ai.llm import make_provider
    from .governance.rights_guard import apply_rights_limits
    from .ingestion.collect import (
        _MANDATORY,
        _NOTICE,
        _collect,
        _digest_path,
        _event,
        _load_registry,
        _load_review_flags,
        _load_topic_pack,
        _queue_lock,
        _run_id,
        _window,
        _write_json,
        review_queue_path,
        run_scope,
    )
    from .processing.audit import _audit_report, _write_audit
    from .processing.curation import dedupe_quotes, review_badge
    from .processing.digest import write_digest
    from .processing.export import atomic_write_text, local_store_path, write_jsonl

    provisional_scope = run_scope(
        source_mode, tier=tier, source_ids=source_ids, provenance=provenance
    )
    active_topic_id = str(
        topic_id or getattr(settings, "active_topic", DEFAULT_TOPIC_ID) or DEFAULT_TOPIC_ID
    )
    topic_pack = _load_topic_pack(settings, active_topic_id, strict=topic_id is not None)
    provisional_run_id = run_id or _run_id(
        report_date,
        settings,
        topic_id=active_topic_id,
        provenance=provenance,
        source_mode=source_mode,
        scope=provisional_scope,
    )
    records, coverage, gaps, events, rejected, offsets = _collect(
        settings,
        run_id=provisional_run_id,
        report_date=report_date,
        topic_pack=topic_pack,
        source_mode=source_mode,
        tier=tier,
        prefetched_refs=prefetched_refs,
        source_ids=source_ids,
        session_context=session_context,
    )
    if (source_mode in {"live", "blobs", "archive"} or provenance != "fixture") and any(
        record.provenance == "fixture" for record in records
    ):
        raise ConfigError(
            "live/blob run collected a fixture-stamped record; refusing to publish it as live"
        )
    collected_provenances = {record.provenance for record in records}
    if len(collected_provenances) == 1:
        only_provenance = next(iter(collected_provenances))
        if only_provenance != "fixture":
            provenance = cast(AcquisitionProvenance, only_provenance)

    # Collection may settle the run-level provenance (e.g. all live_bing records). Only
    # then can the scope and default run ID be final; events carry the provisional ID.
    scope = run_scope(source_mode, tier=tier, source_ids=source_ids, provenance=provenance)
    actual_run_id = run_id or _run_id(
        report_date,
        settings,
        topic_id=active_topic_id,
        provenance=provenance,
        source_mode=source_mode,
        scope=scope,
    )
    if actual_run_id != provisional_run_id:
        for event in events:
            if event.get("run_id") == provisional_run_id:
                event["run_id"] = actual_run_id

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
            provenance=provenance,
            artifact_scope=scope,
            offsets=offsets,
        )

    expected_mandatory = set(_MANDATORY)
    if source_ids is not None:
        expected_mandatory &= set(source_ids)
    if tier is not None:
        expected_mandatory &= {
            row.source_id for row in _load_registry(settings) if row.tier == tier
        }
    quotes_by_source = {str(cov.get("source_id")): cov.get("quotes", 0) for cov in coverage}
    failed_live = (
        source_mode in {"live", "archive"}
        and bool(expected_mandatory)
        and all(not quotes_by_source.get(source_id) for source_id in expected_mandatory)
    )

    rights = apply_rights_limits(records, max_quote_words=settings.max_quote_words)
    kept = dedupe_quotes(rights.kept, window_days=settings.report_window_days)
    store_path = local_store_path(
        settings.export_dir,
        report_date,
        topic_id=active_topic_id,
        provenance=provenance,
        scope=scope,
    )
    write_jsonl(kept, store_path)

    now = datetime.combine(report_date, time(12, 0), tzinfo=UTC)
    review_flags = _load_review_flags(settings, provenance)

    # DEC-17 / FR-2.4: only compare quotes attributed to the same speaker.
    # Offline runs escalate and produce no new flags; failures are recorded, never hidden.
    _pairs_evaluated = 0
    _pairs_skipped = 0
    try:
        from .risk_controls.obsolescence import propose_obsolescence as _propose_obsolescence

        _judge = _call_factory(make_judge, settings, session_context=session_context)
        _speakers = {
            quote_id: str(meta.get("speaker") or "")
            for quote_id, meta in offsets.items()
            if isinstance(meta, Mapping)
        }
        # Bound every judge mode: even the free local stand-in must not trigger an
        # unbounded O(n^2) scan. Twenty pairs is the disclosed cap for all modes.
        _max_pairs = 20
        _proposal = _propose_obsolescence(
            list(kept), now=now, judge=_judge, speakers=_speakers, max_pairs=_max_pairs
        )
        _new_flags = _proposal.flags
        _pairs_evaluated = _proposal.pairs_evaluated
        _pairs_skipped = _proposal.pairs_skipped
        events.append(
            _event(
                run_id=actual_run_id,
                trace_id=actual_run_id,
                source_id="pipeline",
                step="obsolescence",
                status="proposed",
                pairs_evaluated=_pairs_evaluated,
                pairs_skipped=_pairs_skipped,
                license_reference="(none)",
            )
        )
        _queue_path = review_queue_path(settings, provenance)
        with _queue_lock(_queue_path):
            # Re-read under the lock: a concurrent operator's verdict/flag must survive.
            _current_flags = _load_review_flags(settings, provenance)
            _existing_ids = {f.evidence.old_quote_id for f in _current_flags}
            _merged = list(_current_flags)
            for flag in _new_flags:
                if flag.evidence.old_quote_id not in _existing_ids:
                    _merged.append(flag)
                    _existing_ids.add(flag.evidence.old_quote_id)
            review_flags = _merged
            if _merged != _current_flags:
                _write_json(_queue_path, {"flags": [f.model_dump(mode="json") for f in _merged]})
    except Exception as exc:
        events.append(
            _event(
                run_id=actual_run_id,
                trace_id=actual_run_id,
                source_id="pipeline",
                step="obsolescence",
                status="skipped",
                error_type=type(exc).__name__,
                pairs_evaluated=_pairs_evaluated,
                pairs_skipped=_pairs_skipped,
            )
        )

    review_badges: dict[str, str] = {}
    for flag in review_flags:
        badge = review_badge(flag, now=now)
        if badge:
            review_badges[flag.evidence.old_quote_id] = badge

    digest_path = None
    digest_inference_failures: list[str] = []
    if not failed_live:
        # A failed live run must not publish a digest that looks like normal output.
        digest_path = write_digest(
            kept,
            report_date=report_date,
            window_start=_window(report_date, settings)[0],
            window_end=report_date,
            out_path=_digest_path(
                settings,
                report_date,
                topic_id=active_topic_id,
                provenance=provenance,
                scope=scope,
            ),
            sections_csv=settings.sections_csv,
            topic_pack=topic_pack,
            provider=_call_factory(
                make_provider, settings, topic_pack=topic_pack, session_context=session_context
            ),
            obsolescence_flags=review_flags,
            review_badges=review_badges,
            notice=_LIVE_NOTICE if provenance != "fixture" else _NOTICE,
            now=now,
            inference_failures=digest_inference_failures,
        )
        # Quotes are already written; a provider failure is disclosed, never retried.
        for section_id in digest_inference_failures:
            events.append(
                _event(
                    run_id=actual_run_id,
                    trace_id=actual_run_id,
                    source_id="pipeline",
                    step="digest_inference",
                    status="failed",
                    section_id=section_id,
                    error_type="InferenceProviderError",
                )
            )

    logs_path = settings.logs_dir / f"{actual_run_id}.jsonl"
    atomic_write_text(
        logs_path,
        "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for event in events
        ),
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
        provenance=provenance,
        source_mode=source_mode,
        scope=scope,
    )
    if failed_live:
        report["artifacts"]["digest"] = ""
    audit_json_path, audit_md_path = _write_audit(
        settings, report, provenance=provenance, scope=scope
    )
    _write_json(audit_json_path.parent / f"{actual_run_id}.json", report)

    gates_pass = bool(report["checks"].get("gates_pass", False))
    status = "failed" if failed_live or not gates_pass else "partial" if gaps else "succeeded"
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
        provenance=provenance,
        artifact_scope=scope,
        review_flags=tuple(review_flags),
        offsets=offsets,
        store_path=store_path,
        digest_path=digest_path,
        logs_path=logs_path,
        audit_json_path=audit_json_path,
        audit_md_path=audit_md_path,
        checks=dict(report["checks"]),
    )

    should_persist = (
        settings.persist_to_postgres if persist_to_postgres is None else persist_to_postgres
    )
    if should_persist:
        result.persistence.update(
            _persist_result(result, settings, pipeline=pipeline, session_context=session_context)
        )
    return result
