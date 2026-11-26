"""Neutral source-collection service shared by the CLI and the daily pipeline.

This module owns discovery, topic gating, verification and record building. The CLI keeps
thin compatibility re-exports at ``media_quotes.cli.run`` for existing callers.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from uuid import NAMESPACE_URL, uuid5

from ..adapters import BlobCache, FixtureSource, build_adapter
from ..ai.translation import TranslationProvider, make_translation_provider
from ..config import DEFAULT_TOPIC_ID, PROJECT_ROOT, Settings
from ..enums import AcquisitionProvenance
from ..exceptions import ConfigError
from ..ingestion.fetch import fetch_policy_from
from ..ingestion.live import LiveSourceResult, collect_live_source
from ..models import ExportQuoteRecord, QuoteCandidate
from ..pipeline import (
    JudgeProvider,
    LanguageDecisionSnapshot,
    SourceTopicRoute,
    TopicAssessment,
    TranslationSnapshot,
    _call_factory,
    _field,
    _strings,
    _text,
    assess_topic,
    detect_language,
    load_source_topic_routes,
    load_topic_rules,
    make_judge,
    normalise_language_decision,
    normalise_translation_result,
    translate_quote,
)
from ..processing.export import content_hash, quote_record
from ..processing.verification_stages import verify_pipeline
from ..registry import SourceRegistryRow
from ..risk_controls.models import ObsolescenceFlag
from ..source_configs import DEFAULT_SOURCES_DIR, SourceConfig, load_routes_from_sources
from ..text_utils import normalise_text
from ..topics import DEFAULT_TOPICS_DIR, TopicPack, load_all_topic_rules

if TYPE_CHECKING:
    from ..db.resources import SessionContextFactory

from ..cli.common import (
    _MANDATORY,
    _NOTICE,
    _digest_path,
    _event,
    _fixture_mode,
    _gap_for,
    _gap_record,
    _load_registry,
    _load_source_configs,
    _load_topic_pack,
    _queue_lock,
    _run_id,
    _trace_id,
    _window,
    _write_json,
    load_review_flags,
    review_queue_path,
    run_scope,
)

# Explicit re-exports: the CLI shim imports these names for compatibility.
__all__ = [
    "_DEFAULT_LOAD_SOURCE_TOPIC_ROUTES",
    "_DEFAULT_LOAD_TOPIC_RULES",
    "_MANDATORY",
    "_NOTICE",
    "TopicGate",
    "TopicGateOutcome",
    "_Unset",
    "_article_for_topic",
    "_collect",
    "_detect_language_safe",
    "_digest_path",
    "_event",
    "_format_run_summary",
    "_gate_article",
    "_load_registry",
    "_load_review_flags",
    "_load_topic_pack",
    "_path_setting",
    "_process_source",
    "_queue_lock",
    "_run_id",
    "_select_topic_route",
    "_setting",
    "_topic_event_fields",
    "_topic_gate",
    "_topic_judge",
    "_translation_for_quote",
    "_window",
    "_write_json",
    "build_adapter",
    "collect_live_source",
    "detect_language",
    "fetch_policy_from",
    "load_source_topic_routes",
    "load_topic_rules",
    "make_translation_provider",
    "review_queue_path",
    "run_scope",
    "translate_quote",
    "verify_pipeline",
]

_DEFAULT_LOAD_SOURCE_TOPIC_ROUTES = load_source_topic_routes
_DEFAULT_LOAD_TOPIC_RULES = load_topic_rules


class _Unset:
    """Marker for lazily built parameters so explicit ``None`` stays distinguishable."""


_UNSET = _Unset()


def _load_review_flags(
    settings: Settings, provenance: AcquisitionProvenance = "fixture"
) -> list[ObsolescenceFlag]:
    return load_review_flags(settings, provenance)


@dataclass(frozen=True, slots=True)
class TopicGate:
    """Approved source routes plus the versioned rules they reference."""

    routes_by_source: dict[str, list[SourceTopicRoute]]
    rules: dict[str, dict[str, Any]]
    preferred_rule_id: str = ""


@dataclass(frozen=True, slots=True)
class TopicGateOutcome:
    assessment: TopicAssessment
    route: SourceTopicRoute | None = None


def _setting(settings: Settings, name: str, default: Any) -> Any:
    """Read a new setting even when running against an older Settings object."""
    return getattr(settings, name, default)


def _path_setting(settings: Settings, name: str) -> Path | None:
    value = _setting(settings, name, None)
    if value is None or value == "":
        return None
    return value if isinstance(value, Path) else Path(str(value))


def _topic_gate(settings: Settings, *, topic_pack: TopicPack | None = None) -> TopicGate | None:
    routes_path = _path_setting(settings, "source_topics_csv")
    rules_path = _path_setting(settings, "topic_rules_path")
    sources_dir = _path_setting(settings, "sources_dir")
    topics_dir = _path_setting(settings, "topics_dir")
    if routes_path is None and sources_dir is None:
        return None
    if rules_path is None and topics_dir is None and topic_pack is None:
        return None

    default_routes_csv = (PROJECT_ROOT / "data" / "source_topic_routes.csv").resolve()
    default_rules_json = (PROJECT_ROOT / "data" / "topic_rules.json").resolve()
    custom_routes_csv = routes_path is not None and routes_path.resolve() != default_routes_csv
    custom_rules_json = rules_path is not None and rules_path.resolve() != default_rules_json

    routes_patched = load_source_topic_routes is not _DEFAULT_LOAD_SOURCE_TOPIC_ROUTES
    rules_patched = load_topic_rules is not _DEFAULT_LOAD_TOPIC_RULES

    try:
        if (
            not routes_patched
            and sources_dir is not None
            and sources_dir.is_dir()
            and not (custom_routes_csv and sources_dir.resolve() == DEFAULT_SOURCES_DIR.resolve())
        ):
            routes: dict[str, list[SourceTopicRoute]] = cast(
                dict[str, list[SourceTopicRoute]],
                load_routes_from_sources(sources_dir, fallback_csv=routes_path),
            )
        elif routes_path is not None:
            routes = load_source_topic_routes(routes_path)
        else:
            routes = {}

        if (
            not rules_patched
            and topics_dir is not None
            and topics_dir.is_dir()
            and not (custom_rules_json and topics_dir.resolve() == DEFAULT_TOPICS_DIR.resolve())
        ):
            rules = load_all_topic_rules(topics_dir, fallback_rules_path=rules_path)
        elif rules_path is not None:
            rules = load_topic_rules(rules_path)
        else:
            rules = {}
    except (ImportError, FileNotFoundError, ValueError, TypeError) as exc:
        raise ConfigError(f"topic gate configuration is invalid: {exc}") from exc

    preferred_rule_id = ""
    if topic_pack is not None:
        preferred_rule_id = topic_pack.relevance.rule_id
        rule_dict = topic_pack.to_rule_dict()
        rules[preferred_rule_id] = rule_dict
        rules.setdefault(topic_pack.topic_id, rule_dict)
    return TopicGate(
        routes_by_source=routes,
        rules=rules,
        preferred_rule_id=preferred_rule_id,
    )


def _topic_judge(settings: Settings) -> JudgeProvider | None:
    if str(_setting(settings, "ai_judge_mode", "off")).strip().lower() != "decision":
        return None
    try:
        return make_judge(settings)
    except (ImportError, ValueError, KeyError, TypeError):
        return None


def _select_topic_route(gate: TopicGate | None, source_id: str) -> SourceTopicRoute | None:
    if gate is None:
        return None
    source_routes = gate.routes_by_source.get(source_id, [])
    if gate.preferred_rule_id:
        for route in source_routes:
            if (
                _field(route, "approved", False)
                and _text(_field(route, "topic_rule_id", "")) == gate.preferred_rule_id
            ):
                return route
        return None
    for route in source_routes:
        if _field(route, "approved", False) and _text(_field(route, "topic_rule_id", "")):
            return route
    return None


def _article_for_topic(
    metadata: dict[str, Any], *, text: str, canonical_url: str, title: str, ref: str
) -> dict[str, Any]:
    tags_raw = (
        metadata.get("tags")
        or metadata.get("topics")
        or metadata.get("keywords")
        or metadata.get("tags_list")
        or []
    )
    return {
        "article_text": text,
        "title": title,
        "canonical_url": canonical_url,
        "tags": list(_strings(tags_raw)),
        "article_ref": ref,
        "language": str(metadata.get("language") or ""),
    }


def _gate_article(
    settings: Settings,
    gate: TopicGate | None,
    *,
    source_id: str,
    metadata: dict[str, Any],
    text: str,
    canonical_url: str,
    title: str,
    ref: str,
    judge: JudgeProvider | None = None,
    allow_default_rule: bool = False,
) -> TopicGateOutcome | None:
    route = _select_topic_route(gate, source_id)
    if gate is None:
        return None
    rule_id = _text(_field(route, "topic_rule_id", "")) if route is not None else ""
    rule = gate.rules.get(rule_id)
    if rule is None and allow_default_rule and gate.rules:
        # Only synthetic replay may fall back to the default rule; live data must have
        # an explicit approved route and rule.
        if gate.preferred_rule_id and gate.preferred_rule_id in gate.rules:
            rule = gate.rules[gate.preferred_rule_id]
        else:
            rule_id, rule = next(iter(gate.rules.items()))
    if (route is None and not allow_default_rule) or rule is None:
        return None
    article = _article_for_topic(
        metadata, text=text, canonical_url=canonical_url, title=title, ref=ref
    )
    return TopicGateOutcome(assessment=assess_topic(rule, article, judge=judge), route=route)


def _topic_event_fields(outcome: TopicGateOutcome, row: SourceRegistryRow) -> dict[str, Any]:
    assessment = outcome.assessment
    fields: dict[str, Any] = {
        "topic_status": assessment.status,
        "topic_rule_id": assessment.rule_id,
        "topic_rule_version": assessment.rule_version,
        "topic_score": assessment.score,
        "matched_terms": list(assessment.matched_terms),
        "reasons": list(assessment.reasons),
        "route_id": _text(_field(outcome.route, "route_id", "")) if outcome.route else "",
        "translation_rights_confirmed": bool(
            _field(outcome.route, "translation_rights_confirmed", False)
        )
        if outcome.route
        else False,
        "escalated": assessment.escalated,
        "license_reference": row.license_reference or "(none)",
    }
    if assessment.judge is not None:
        fields.update(
            {
                "judge_actor": assessment.judge.actor,
                "judge_verdict": assessment.judge.verdict,
                "judge_confidence": assessment.judge.confidence,
                "judge_model_snapshot": assessment.judge.model_snapshot,
                "judge_prompt_version": assessment.judge.prompt_version,
                "judge_input_hash": assessment.judge.input_hash,
                "judge_output_hash": assessment.judge.output_hash,
            }
        )
    return fields


def _detect_language_safe(text: str) -> LanguageDecisionSnapshot:
    try:
        return normalise_language_decision(detect_language(text))
    except (ImportError, ValueError, TypeError):
        return LanguageDecisionSnapshot(language="", confidence=0.0, evidence="unavailable")


def _translation_for_quote(
    settings: Settings,
    *,
    quote_text: str,
    language: LanguageDecisionSnapshot,
    route: SourceTopicRoute | None,
    run_id: str,
    trace_id: str,
    row: SourceRegistryRow,
    events: list[dict[str, Any]],
    translator: TranslationProvider | None = None,
) -> TranslationSnapshot | None:
    mode = str(_setting(settings, "translation_mode", "off")).strip().lower()
    if mode != "translate":
        return None
    if route is None or not _field(route, "translation_rights_confirmed", False):
        return None
    if not language.language or language.language == "en":
        return None
    try:
        result = normalise_translation_result(
            translate_quote(
                quote_text,
                source_language=language.language,
                settings=settings,
                provider=translator,
            )
        )
    except (ImportError, ValueError, TypeError) as exc:
        result = TranslationSnapshot(
            text="",
            source_language=language.language,
            status="failed",
            model_snapshot="",
            prompt_version=str(_setting(settings, "translation_prompt_version", "") or ""),
            content_hash="",
            error=str(exc),
            usable=False,
        )
    events.append(
        _event(
            run_id=run_id,
            trace_id=trace_id,
            source_id=row.source_id,
            step="translation",
            status="translated" if result.usable else "failed",
            quote_language=language.language,
            translation_status=result.status,
            translation_model_snapshot=result.model_snapshot,
            translation_prompt_version=result.prompt_version,
            translation_hash=result.content_hash,
            error=result.error,
            license_reference=row.license_reference or "(none)",
        )
    )
    return result


def _process_source(  # pragma: no cover
    row: SourceRegistryRow,
    *,
    settings: Settings,
    run_id: str,
    window_start: date,
    report_date: date,
    used_quote_ids: set[str],
    topic_pack: TopicPack | None = None,
    source_config: SourceConfig | None = None,
    source_mode: str = "fixtures",
    refs: list[str] | None = None,
    provenance: AcquisitionProvenance = "fixture",
    discovery_route: str = "",
    gate: TopicGate | _Unset | None = _UNSET,
    judge: JudgeProvider | _Unset | None = _UNSET,
    translator: TranslationProvider | None = None,
) -> tuple[
    list[ExportQuoteRecord],
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
    dict[str, dict[str, Any]],
]:
    """Process one source through discovery, fixture fetch, two-stage verification and metadata."""
    use_blobs = source_mode in {"blobs", "live"}
    replay = False if use_blobs else _fixture_mode(row, settings)
    active_topic_id = (
        topic_pack.topic_id
        if topic_pack is not None
        else str(_setting(settings, "active_topic", DEFAULT_TOPIC_ID))
    )
    default_section = topic_pack.default_section if topic_pack is not None else "other"
    events: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    gap_keys: set[tuple[str, ...]] = set()

    def add_gap(gap: dict[str, Any]) -> None:
        # One gap per source/reason: fixture articles repeat the same rights/topic problem.
        key = tuple(
            str(gap.get(field) or "")
            for field in ("source_id", "gap_reason", "detail", "topic_status")
        )
        if key not in gap_keys:
            gap_keys.add(key)
            gaps.append(gap)

    records: list[ExportQuoteRecord] = []
    offsets: dict[str, dict[str, Any]] = {}
    rejected = 0
    failed_refs: list[str] = []

    if not row.usable and not replay:
        # T0 gate: never call discovery/fetch for a disabled source with no fixture.
        add_gap(_gap_record(row, "no permitted route and no fixture available"))
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
                "provenance": provenance,
                "discovery_route": discovery_route,
                "status": "blocked",
                "gap_reason": _gap_for(row)[1],
                "mandatory": row.source_id in _MANDATORY,
                "topic_status": "skipped",
                "topic_rule_version": "",
                "topic_score": 0.0,
                "matched_terms": [],
                "topic_gate": {"accepted": 0, "uncertain": 0, "rejected": 0, "skipped": 0},
            },
            gaps,
            events,
            rejected,
            offsets,
        )

    if not row.usable:
        add_gap(_gap_record(row, "fixture replay only; the T0 gate refuses live contact"))

    article_root = settings.blob_dir if use_blobs else settings.fixtures_dir
    blob_cache = BlobCache(settings.blob_dir) if use_blobs else None
    adapter = build_adapter(
        row,
        FixtureSource(article_root),
        replay=replay,
        topic_pack=topic_pack,
        source_config=source_config,
        extraction_fallback=settings.extraction_fallback,
    )
    article_refs = adapter.discover(window_start, report_date) if refs is None else refs
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
                fixture_count=len(article_refs),
                message="blocked mandatory source replayed from synthetic fixtures",
            )
        )
    elif article_refs:
        events.append(
            _event(
                run_id=run_id,
                trace_id=f"trace-source-{row.source_id}",
                source_id=row.source_id,
                step="discover",
                status="collected",
                mode=adapter.mode,
                provenance=provenance,
                discovery_route=discovery_route,
                license_reference=row.license_reference or "(none)",
                fixture_count=len(article_refs),
                message="articles supplied by the live collector; no fixture discovery used",
            )
        )

    policy_flags = dict(adapter.policy_flags())
    policy_flags["fixture_replay"] = replay
    verified_at = datetime.combine(report_date, time(12, 0), tzinfo=UTC)
    if isinstance(gate, _Unset):
        gate = _topic_gate(settings, topic_pack=topic_pack)
    if isinstance(judge, _Unset):
        judge = _topic_judge(settings)
    topic_counts = {"accepted": 0, "uncertain": 0, "rejected": 0, "skipped": 0}
    current_route: SourceTopicRoute | None = None
    topic_terms: set[str] = set()
    topic_rule_version = ""
    topic_score = 0.0
    route = discovery_route

    for ref in article_refs:
        try:
            trace_id = _trace_id(row.source_id, ref)
            raw = adapter.fetch(ref)
            sidecar_meta: dict[str, Any] = {}
            if blob_cache is not None:
                ref_stem = Path(ref.rsplit("/", 1)[-1]).stem
                sidecar_meta = blob_cache.read_meta(row.source_id, ref_stem)
            sidecar_url = str(sidecar_meta.get("url") or "") or None
            route = discovery_route or str(sidecar_meta.get("discovered_via") or "")
            metadata = (
                adapter.parse_metadata(raw, source_url=sidecar_url)
                if sidecar_url is not None
                else adapter.parse_metadata(raw)
            )
            if sidecar_meta:
                if not metadata.get("title") and sidecar_meta.get("title"):
                    metadata["title"] = str(sidecar_meta["title"])
                if not metadata.get("canonical_url") and sidecar_meta.get("url"):
                    metadata["canonical_url"] = str(sidecar_meta["url"])
            if metadata.get("quality_ok") is False:
                rejected += 1
                continue
            text = str(metadata.get("article_text") or "")
            canonical_url = str(metadata.get("canonical_url") or "")
            article_ref_name = str(metadata.get("title") or ref)
            raw_hash = hashlib.sha256(raw).hexdigest()
            extraction_method = str(metadata.get("extraction_method") or "fixture_html_v1")
            extractor_version = str(metadata.get("extractor_version") or "fixture-replay-v1")
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
                    provenance=provenance,
                    discovery_route=route,
                    license_reference=row.license_reference or "(none)",
                )
            )

            # Topic gate runs immediately after metadata parsing and before any candidate is built.
            outcome = _gate_article(
                settings,
                gate,
                source_id=row.source_id,
                metadata=metadata,
                text=text,
                canonical_url=canonical_url,
                title=article_ref_name,
                ref=ref,
                judge=judge,
                allow_default_rule=replay,
            )
            if outcome is None:
                topic_counts["skipped"] += 1
                gap = _gap_record(row, "topic_gate unavailable: no approved route or rule")
                gap.update({"license_reference": row.license_reference or "(none)"})
                add_gap(gap)
                rejected += 1
                events.append(
                    _event(
                        run_id=run_id,
                        trace_id=trace_id,
                        source_id=row.source_id,
                        step="topic_gate",
                        status="config_error",
                        license_reference=row.license_reference or "(none)",
                        message="topic gate refused unrouted article; no quote extracted",
                    )
                )
                current_route = None
                continue
            else:
                current_route = outcome.route
                assessment = outcome.assessment
                event_fields = _topic_event_fields(outcome, row)
                events.append(
                    _event(
                        run_id=run_id,
                        trace_id=trace_id,
                        source_id=row.source_id,
                        step="topic_gate",
                        status=assessment.status,
                        **event_fields,
                    )
                )
                topic_counts[assessment.status] += 1
                topic_terms.update(assessment.matched_terms)
                topic_rule_version = assessment.rule_version
                topic_score = assessment.score
                if assessment.status != "accepted":
                    gap = _gap_record(
                        row,
                        f"topic_gate {assessment.status}: "
                        + ("; ".join(assessment.reasons) or "no reasons recorded"),
                    )
                    gap.update(
                        {
                            "topic_status": assessment.status,
                            "topic_rule_id": assessment.rule_id,
                            "topic_rule_version": assessment.rule_version,
                            "topic_score": assessment.score,
                            "matched_terms": list(assessment.matched_terms),
                            "escalated": assessment.escalated,
                            "license_reference": row.license_reference or "(none)",
                        }
                    )
                    add_gap(gap)
                    rejected += 1
                    continue

            spans_raw = metadata.get("quote_spans", [])
            spans = (
                [s for s in spans_raw if isinstance(s, dict)] if isinstance(spans_raw, list) else []
            )
            spans_by_text: dict[str, list[dict[str, Any]]] = {}
            for span in spans:
                norm = normalise_text(str(span.get("quote_text") or ""))
                spans_by_text.setdefault(norm, []).append(span)
            # Keep the span actually consumed for each candidate; duplicate quotes reuse
            # the same text but carry different section/speaker metadata.
            matched_spans: dict[int, dict[str, Any]] = {}

            candidates: list[QuoteCandidate] = []
            for found in adapter.extract_quotes(text):
                quote_text = str(found.get("quote_text") or "")
                norm = normalise_text(quote_text)
                matching_spans = spans_by_text.get(norm)
                span = matching_spans.pop(0) if matching_spans else {}
                candidate = QuoteCandidate(
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
                matched_spans[id(candidate)] = span
                candidates.append(candidate)

            article_policy = dict(policy_flags)
            article_policy["original_reporting_flag"] = (
                metadata.get("original_reporting_flag")
                or sidecar_meta.get("original_reporting_flag")
                or (source_config.original_reporting_flag if source_config is not None else None)
                or "first-hand"
            )
            pipeline_outcome = verify_pipeline(
                candidates,
                text=text,
                source_row=row,
                policy_flags=article_policy,
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
                language = _detect_language_safe(candidate.quote_text)
                translation = _translation_for_quote(
                    settings,
                    quote_text=candidate.quote_text,
                    language=language,
                    route=current_route,
                    run_id=run_id,
                    trace_id=trace_id,
                    row=row,
                    events=events,
                    translator=translator,
                )
                span = matched_spans.get(id(candidate), {})
                publication_date = str(metadata.get("publication_date") or report_date.isoformat())[
                    :10
                ]
                id_seed = hashlib.sha256(
                    "|".join(
                        (
                            row.source_id,
                            canonical_url,
                            candidate.quote_text,
                            str(candidate.char_start),
                            str(candidate.char_end),
                            raw_hash,
                        )
                    ).encode("utf-8")
                ).hexdigest()
                sequence = int(id_seed[:8], 16) % 10000
                first_sequence = sequence
                while (
                    quote_id := f"Q-{publication_date.replace('-', '')}-{sequence:04d}"
                ) in used_quote_ids:
                    sequence = (sequence + 1) % 10000
                    if sequence == first_sequence:
                        raise ConfigError(f"quote id space exhausted for {publication_date}")
                used_quote_ids.add(quote_id)
                offsets[quote_id] = {
                    "char_start": candidate.char_start,
                    "char_end": candidate.char_end,
                    "dom_path": candidate.dom_path,
                    "speaker": candidate.speaker,
                    "attribution_span": candidate.attribution_span,
                    "content_hash": raw_hash,
                    "raw_blob_ref": ref,
                    "extracted_text": text,
                    "extraction_method": extraction_method,
                    "extractor_version": extractor_version,
                    "article_metadata": {
                        "fixture_replay": replay,
                        "mode": adapter.mode,
                        "topic_id": active_topic_id,
                    },
                }
                section_id = str(span.get("section_id") or default_section)
                if topic_pack is not None and section_id not in topic_pack.section_ids():
                    # A fixture may carry a section from a different topic pack; keep the
                    # record consistent with the topic it was admitted under.
                    section_id = default_section
                accepted_topic = (
                    outcome.assessment
                    if outcome is not None and outcome.assessment.status == "accepted"
                    else None
                )
                topic_updates: dict[str, Any] = {
                    "quote_language": language.language,
                    "language_confidence": language.confidence,
                }
                if accepted_topic is not None:
                    topic_updates.update(
                        {
                            "topic_status": "accepted",
                            "topic_rule_version": accepted_topic.rule_version,
                            "topic_score": accepted_topic.score,
                            "matched_terms": list(accepted_topic.matched_terms),
                        }
                    )
                if translation is not None:
                    topic_updates.update(
                        {
                            "quote_text_en": translation.text if translation.usable else "",
                            "translation_model_snapshot": translation.model_snapshot,
                            "translation_prompt_version": translation.prompt_version,
                            "translation_hash": translation.content_hash,
                            "translation_status": (
                                "translated"
                                if translation.usable
                                else translation.status or "failed"
                            ),
                        }
                    )
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
                        original_reporting_flag=str(article_policy["original_reporting_flag"]),
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
                        notes=(
                            "synthetic fixture replay"
                            if replay
                            else ("live blob ingest" if use_blobs else "offline replay")
                        ),
                        topic_id=active_topic_id,
                        provenance=provenance,
                    ).model_copy(update=topic_updates)
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
                        provenance=provenance,
                        discovery_route=route,
                        license_reference=row.license_reference or "(none)",
                    )
                )
        except ConfigError:
            raise
        except Exception as exc:
            rejected += 1
            failed_refs.append(ref)
            add_gap(_gap_record(row, f"fetch_parse error for {ref}: {exc}"))
            events.append(
                _event(
                    run_id=run_id,
                    trace_id=_trace_id(row.source_id, ref),
                    source_id=row.source_id,
                    step="fetch_parse",
                    status="error",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    canonical_url=ref,
                    license_reference=row.license_reference or "(none)",
                )
            )
            continue
    if not article_refs and row.usable:
        detail = (
            f"no articles were supplied by the {provenance} collector"
            if use_blobs
            else "no permitted articles found in the replay window"
        )
        if use_blobs and discovery_route:
            detail += f" via {discovery_route}"
        gap = _gap_record(row, detail)
        if use_blobs:
            gap.update({"provenance": provenance, "discovery_route": discovery_route})
        gaps.append(gap)
    coverage = {
        "source_id": row.source_id,
        "expected_articles": len(article_refs),
        "discovered": len(article_refs),
        "processed": len(article_refs) - len(failed_refs),
        "quotes": sum(1 for record in records if record.source_id == row.source_id),
        "last_check_at": verified_at.isoformat(),
        "provenance": provenance,
        "discovery_route": route,
        "status": (
            "degraded"
            if replay or not row.usable or (use_blobs and not article_refs) or failed_refs
            else "ok"
        ),
        "gap_reason": "rights_blocked" if replay else "",
        "mandatory": row.source_id in _MANDATORY,
        "topic_status": (
            "rejected"
            if topic_counts["rejected"] or topic_counts["uncertain"]
            else "accepted"
            if topic_counts["accepted"]
            else "skipped"
        ),
        "topic_rule_version": topic_rule_version,
        "topic_score": topic_score,
        "matched_terms": sorted(topic_terms),
        "topic_gate": dict(topic_counts),
    }
    return records, coverage, gaps, events, rejected, offsets


def _collect(  # pragma: no cover
    settings: Settings,
    *,
    run_id: str,
    report_date: date,
    topic_pack: TopicPack | None = None,
    source_mode: str = "fixtures",
    tier: int | None = None,
    prefetched_refs: Mapping[str, list[str]] | None = None,
    source_ids: set[str] | None = None,
    session_context: SessionContextFactory | None = None,
) -> tuple[
    list[ExportQuoteRecord],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    int,
    dict[str, dict[str, Any]],
]:
    if source_mode not in {"fixtures", "blobs", "live", "archive"}:
        raise ConfigError(f"unsupported source_mode {source_mode!r}")
    window_start, _ = _window(report_date, settings)
    records: list[ExportQuoteRecord] = []
    coverage: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    rejected = 0
    used_quote_ids: set[str] = set()
    offsets: dict[str, dict[str, Any]] = {}
    # One translator for the whole collection; providers own clients/caches and must not be
    # rebuilt for every quote. Invalid configuration stays a per-quote failure below.
    translator: TranslationProvider | None = None
    if str(_setting(settings, "translation_mode", "off")).strip().lower() == "translate":
        try:
            translator = _call_factory(
                make_translation_provider, settings, session_context=session_context
            )
        except (ConfigError, ValueError, TypeError):
            translator = None
    source_configs = _load_source_configs(settings)
    active_pack = topic_pack if topic_pack is not None else _load_topic_pack(settings)
    gate = _topic_gate(settings, topic_pack=active_pack)
    judge = _topic_judge(settings)
    topic_id = (
        active_pack.topic_id
        if active_pack is not None
        else str(_setting(settings, "active_topic", DEFAULT_TOPIC_ID))
    )
    rows = _load_registry(settings)
    if tier is not None:
        rows = [row for row in rows if row.tier == tier]
    if source_ids is not None:
        rows = [row for row in rows if row.source_id in source_ids]
    for row in rows:
        refs: list[str] | None = None
        row_provenance: AcquisitionProvenance = (
            "live_feed" if source_mode in {"blobs", "live", "archive"} else "fixture"
        )
        discovery_route = ""
        collector_errors: list[str] = []
        if source_mode == "archive":
            # Historical refs were fetched by the backfill orchestrator; no live discovery.
            refs = list((prefetched_refs or {}).get(row.source_id, []))
            discovery_route = "archive"
        elif source_mode == "live" and row.usable:
            config = source_configs.get(row.source_id)
            feed_url = (config.discovery_feed_url(topic_id) if config else "") or row.feed_url
            extra_hosts = frozenset(
                [*config.content_hosts, *config.redirect_hosts] if config else []
            )
            try:
                outcome = collect_live_source(
                    row,
                    feed_url=feed_url,
                    topic_pack=active_pack,
                    policy=fetch_policy_from(settings),
                    cache=BlobCache(settings.blob_dir),
                    limit=settings.live_article_limit,
                    source_config=config,
                    extra_hosts=extra_hosts,
                )
            except Exception as exc:  # a failed collector becomes a disclosed gap, not a crash
                outcome = LiveSourceResult([], [], "live_feed", "official_feed", [str(exc)])
            row_provenance = outcome.provenance
            discovery_route = outcome.route
            collector_errors = list(outcome.errors)
            refs = [
                f"fixture://{row.source_id}/{Path(article.blob_path).name}"
                for article in outcome.articles
                if not article.error and article.blob_path
            ]
        found, cov, row_gaps, row_events, row_rejected, row_offsets = _process_source(
            row,
            settings=settings,
            run_id=run_id,
            window_start=window_start,
            report_date=report_date,
            used_quote_ids=used_quote_ids,
            topic_pack=active_pack,
            source_config=source_configs.get(row.source_id),
            source_mode="blobs" if source_mode in {"live", "archive"} else source_mode,
            refs=refs,
            provenance=row_provenance,
            discovery_route=discovery_route,
            gate=gate,
            judge=judge,
            translator=translator,
        )
        for error in collector_errors:
            gap = _gap_record(row, f"live collector error: {error}")
            gap.update({"provenance": row_provenance, "discovery_route": discovery_route})
            row_gaps.append(gap)
        records.extend(found)
        coverage.append(cov)
        gaps.extend(row_gaps)
        events.extend(row_events)
        rejected += row_rejected
        offsets.update(row_offsets)
    records.sort(key=lambda item: (item.publication_date, item.quote_id))
    return records, coverage, gaps, events, rejected, offsets


def _format_run_summary(result: Any, *, verbose: bool) -> list[str]:
    """Render a pipeline result as short, plain-English console lines.

    Pure and I/O-free so the operator-facing output can be checked in tests
    without running the pipeline.
    """
    lines: list[str] = []

    if result.dry_run:
        lines.append(f"Dry run {result.run_id} — practice only.")
        lines.append(f"  Topic: {result.topic_id}")
        lines.append("What we found:")
        lines.append(f"  {len(result.records)} quote(s) that would be saved")
        lines.append(f"  {result.rejected} candidate(s) rejected")
        lines.append(f"  {len(result.gaps)} coverage gap(s)")
        for gap in result.gaps:
            lines.append(f"  gap: {gap['source_id']} ({gap['gap_reason']}) — {gap['detail']}")
        lines.append("No files were written (practice only).")
        return lines

    if "articles" in result.persistence:
        lines.append(
            f"Saved to Postgres: {result.persistence['articles']} article(s), "
            f"{result.persistence['stored_quotes']} quote(s) stored."
        )

    is_fixture = result.provenance == "fixture"
    source = "fixture pages" if is_fixture else str(result.provenance)
    lines.append(f"Practice newspaper ready — made from {source}.")
    lines.append(f"  Run: {result.run_id}    Topic: {result.topic_id}")
    lines.append(f"  Quotes: {len(result.records)} verified quotes from {source}")
    lines.append(f"  Rejected: {result.rejected} candidate(s)")
    lines.append(f"  Coverage gaps: {len(result.gaps)}")
    if result.status == "partial":
        lines.append("  Status: finished with disclosed gaps (exit code 2).")

    lines.append("Where to find it:")
    if result.digest_path is not None:
        lines.append(f"  Digest: {result.digest_path}")
    lines.append(f"  Store: {result.store_path}")
    lines.append(f"  Logs: {result.logs_path}")
    if verbose:
        lines.append(f"  Audit: {result.audit_md_path}")

    note = (
        "fixture replay logged, no live contact"
        if is_fixture
        else "no synthetic replay; live collection only"
    )
    reported: set[tuple[str, ...]] = set()
    if any(gap.get("mandatory") for gap in result.gaps):
        lines.append("Heads up — these mandatory sources had gaps:")
    for gap in result.gaps:
        if gap.get("mandatory"):
            key = tuple(str(gap.get(field) or "") for field in ("source_id", "gap_reason"))
            if key in reported:
                continue
            reported.add(key)
            lines.append(
                f"COVERAGE GAP: Heads up — {gap['source_id']} is {gap['rights_status']} "
                f"and has a coverage gap: {gap['gap_reason']} ({note})."
            )
    if result.status == "failed":
        lines.append("Run failed: mandatory live sources yielded no quotes; no digest published.")
    return lines
