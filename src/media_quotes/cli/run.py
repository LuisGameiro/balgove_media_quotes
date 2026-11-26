"""Daily pipeline CLI handler."""

from __future__ import annotations

from datetime import date

from ..config import Settings
from ..ingestion.collect import (
    TopicGate,
    TopicGateOutcome,
    _article_for_topic,
    _collect,
    _detect_language_safe,
    _format_run_summary,
    _gate_article,
    _load_registry,
    _load_review_flags,
    _path_setting,
    _process_source,
    _select_topic_route,
    _setting,
    _topic_event_fields,
    _topic_gate,
    _topic_judge,
    _translation_for_quote,
    build_adapter,
    collect_live_source,
    detect_language,
    fetch_policy_from,
    load_source_topic_routes,
    load_topic_rules,
    make_translation_provider,
    translate_quote,
    verify_pipeline,
)
from .common import EXIT_FAILURE, EXIT_GAPS, EXIT_OK

__all__ = [
    "TopicGate",
    "TopicGateOutcome",
    "_article_for_topic",
    "_collect",
    "_detect_language_safe",
    "_format_run_summary",
    "_gate_article",
    "_load_registry",
    "_load_review_flags",
    "_path_setting",
    "_process_source",
    "_select_topic_route",
    "_setting",
    "_topic_event_fields",
    "_topic_gate",
    "_topic_judge",
    "_translation_for_quote",
    "build_adapter",
    "collect_live_source",
    "detect_language",
    "fetch_policy_from",
    "load_source_topic_routes",
    "load_topic_rules",
    "make_translation_provider",
    "translate_quote",
    "verify_pipeline",
]


def _do_run(  # pragma: no cover
    settings: Settings,
    *,
    report_date: date,
    dry_run: bool,
    run_id: str | None,
    verbose: bool,
    topic_id: str | None = None,
    source_mode: str = "fixtures",
    tier: int | None = None,
) -> int:
    from ..pipeline import run_daily_pipeline

    result = run_daily_pipeline(
        settings,
        report_date=report_date,
        run_id=run_id,
        dry_run=dry_run,
        pipeline="mq-run",
        topic_id=topic_id,
        source_mode=source_mode,
        tier=tier,
    )

    for line in _format_run_summary(result, verbose=verbose):
        print(line)
    if result.dry_run:
        return EXIT_OK
    if result.status == "failed":
        return EXIT_FAILURE
    return EXIT_GAPS if result.gaps else EXIT_OK
