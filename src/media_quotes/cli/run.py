"""Daily pipeline handlers for live blobs and explicit fixture replay.

Source-processing helpers now live in :mod:`media_quotes.source_processing`; they are
re-exported here so existing import points and monkeypatch targets keep working. The
aliased globals below stay bound on this module because tests patch them here and the
service resolves them late-bound through :func:`source_processing._compat`.
"""

from datetime import date

from ..adapters import build_adapter as build_adapter
from ..config import Settings
from ..pipeline import (
    assess_topic as assess_topic,
)
from ..pipeline import (
    detect_language as detect_language,
)
from ..pipeline import (
    load_source_topic_routes as load_source_topic_routes,
)
from ..pipeline import (
    load_topic_rules as load_topic_rules,
)
from ..pipeline import (
    translate_quote as translate_quote,
)
from ..source_processing import (
    _DEFAULT_LOAD_SOURCE_TOPIC_ROUTES,
    _DEFAULT_LOAD_TOPIC_RULES,
    TopicGate,
    TopicGateOutcome,
    _article_for_topic,
    _collect,
    _detect_language_safe,
    _gate_article,
    _load_review_flags,
    _path_setting,
    _process_source,
    _select_topic_route,
    _setting,
    _topic_event_fields,
    _topic_gate,
    _topic_judge,
    _translation_for_quote,
)
from .common import EXIT_GAPS, EXIT_OK

__all__ = [
    "_DEFAULT_LOAD_SOURCE_TOPIC_ROUTES",
    "_DEFAULT_LOAD_TOPIC_RULES",
    "TopicGate",
    "TopicGateOutcome",
    "_article_for_topic",
    "_collect",
    "_detect_language_safe",
    "_do_run",
    "_gate_article",
    "_load_review_flags",
    "_path_setting",
    "_process_source",
    "_select_topic_route",
    "_setting",
    "_topic_event_fields",
    "_topic_gate",
    "_topic_judge",
    "_translation_for_quote",
    "assess_topic",
    "build_adapter",
    "detect_language",
    "load_source_topic_routes",
    "load_topic_rules",
    "translate_quote",
]


def _do_run(  # pragma: no cover
    settings: Settings,
    *,
    report_date: date,
    dry_run: bool,
    run_id: str | None,
    verbose: bool,
    topic_id: str | None = None,
    source_mode: str = "live",
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

    if result.dry_run:
        if source_mode == "live":
            print(
                f"Dry run {result.run_id} (topic={result.topic_id}): "
                f"{len(result.coverage)} live source(s) planned; no network calls or files written."
            )
        else:
            print(
                f"Dry run {result.run_id} (topic={result.topic_id}): "
                f"{len(result.records)} quote(s) would be stored, "
                f"{len(result.gaps)} coverage gap(s), {result.rejected} candidate(s) rejected; "
                "no files written."
            )
        for gap in result.gaps:
            print(f"  gap: {gap['source_id']} ({gap['gap_reason']}) — {gap['detail']}")
        return EXIT_OK

    if "articles" in result.persistence:
        print(
            f"Postgres: {result.persistence['articles']} article(s), "
            f"{result.persistence['stored_quotes']} quote(s) persisted"
        )
    quote_label = "fixture quote(s)" if source_mode == "fixtures" else "quote(s)"
    print(
        f"Run {result.run_id} (topic={result.topic_id}): "
        f"{len(result.records)} verified {quote_label}, "
        f"{result.rejected} rejected, {len(result.gaps)} coverage gap(s)."
    )
    print(f"Digest: {result.digest_path}")
    print(f"Store:  {result.store_path}")
    print(f"Logs:   {result.logs_path}")
    if verbose:
        print(f"Audit:  {result.audit_md_path}")
    contact_note = {
        "fixtures": "fixture replay logged, no live contact",
        "blobs": "cached blob ingest, no live contact",
        "live": "live collection attempted",
    }.get(source_mode, "")
    gap_suffix = f" ({contact_note})" if contact_note else ""
    reported: set[tuple[str, ...]] = set()
    for gap in result.gaps:
        if gap.get("mandatory"):
            key = tuple(str(gap.get(field) or "") for field in ("source_id", "gap_reason"))
            if key in reported:
                continue
            reported.add(key)
            print(
                f"COVERAGE GAP: {gap['source_id']} is {gap['rights_status']}; "
                f"{gap['gap_reason']}{gap_suffix}."
            )
    return EXIT_GAPS if result.gaps else EXIT_OK
