"""Operational CLI commands: backfill estimates, sources, topics, digests, exports, dashboard."""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

from ..config import DEFAULT_TOPIC_ID, Settings
from ..digest import write_digest
from ..exceptions import ConfigError
from ..export import local_store_path, read_jsonl, write_jsonl
from ..llm import make_provider
from ..risk_controls import budget_gate, estimate_backfill
from ..source_configs import scaffold_source_config, sync_sources_to_csv
from ..topics import list_topic_packs, load_topic_pack, scaffold_topic_pack
from .common import (
    _ARTICLES_PER_HOUR,
    _ARTICLES_PER_SOURCE_DAY,
    _CALLS_PER_ARTICLE,
    _MAX_BACKFILL_DAYS,
    _NOTICE,
    _PRICE_PER_CALL,
    EXIT_FAILURE,
    EXIT_OK,
    _digest_path,
    _load_registry,
    _load_topic_pack,
    _read_json,
    _window,
)


def _do_backfill(  # pragma: no cover
    settings: Settings,
    *,
    start: date,
    end: date,
    dry_run: bool,
    max_cost: float | None,
) -> int:
    if end < start:
        raise ConfigError("--to must not precede --from")
    span = min((end - start).days + 1, _MAX_BACKFILL_DAYS)
    rows = [row for row in _load_registry(settings) if row.tier < 4]
    base = estimate_backfill(
        articles_per_source_day=_ARTICLES_PER_SOURCE_DAY,
        sources=max(1, len(rows)),
        days=span,
        calls_per_article=_CALLS_PER_ARTICLE,
        price_per_call=_PRICE_PER_CALL,
        articles_per_hour=_ARTICLES_PER_HOUR,
    )
    cached_hashes: set[str] = set()
    for day in range(span):
        current = start + timedelta(days=day)
        for record in read_jsonl(local_store_path(settings.export_dir, current)):
            cached_hashes.add(record.content_hash)
    cache_rate = min(0.9, len(cached_hashes) / max(1, base.calls))
    estimate = estimate_backfill(
        articles_per_source_day=_ARTICLES_PER_SOURCE_DAY,
        sources=max(1, len(rows)),
        days=span,
        calls_per_article=_CALLS_PER_ARTICLE,
        price_per_call=_PRICE_PER_CALL,
        articles_per_hour=_ARTICLES_PER_HOUR,
        expected_cache_hit_rate=cache_rate,
    )
    note = "budget not capped"
    if max_cost is not None:
        note = budget_gate(estimate, max_cost)
    print(
        f"Backfill {start}..{start + timedelta(days=span - 1)} ({span} day(s)): "
        f"~{estimate.articles} articles, {estimate.calls} billed calls, "
        f"${estimate.cost:.2f}, ~{estimate.duration_hours}h."
    )
    print(
        f"Cache-hit summary: {len(cached_hashes)} stored content hash(es), "
        f"assumed hit rate {cache_rate:.2%}."
    )
    print(f"Budget: {note}; live backfill disabled in blocked/degraded mode.")
    if not dry_run:
        print("No source contact or write performed: fixture-only offline replay is the only path.")

    if settings.persist_to_postgres:
        from ..db.repository import finish_pipeline_run, start_pipeline_run
        from ..pipeline import _default_session_context

        run_id = f"backfill-{start.isoformat()}-{end.isoformat()}"
        stats = {
            **asdict(estimate),
            "max_cost": max_cost,
            "budget_note": note,
            "span_days": span,
            "cached_hashes": len(cached_hashes),
        }
        with _default_session_context(settings) as session:
            start_pipeline_run(
                session,
                run_id=run_id,
                pipeline="mq-backfill",
                partition_date=start,
                commit=False,
            )
            finish_pipeline_run(
                session, run_id=run_id, status="succeeded", stats=stats, commit=False
            )
            session.commit()
        print(f"Backfill estimate persisted to Postgres as run {run_id}.")
    return EXIT_OK


def _do_sources(
    settings: Settings,
    *,
    tier: int | None,
    rights_status: str | None,
    action: str = "list",
    source_id: str | None = None,
    source_name: str | None = None,
    homepage_url: str | None = None,
    feed_url: str | None = None,
    language: str = "en",
    country_region: str = "Global",
    topic_id: str | None = None,
) -> int:  # pragma: no cover
    if action == "add":
        if not source_id or not homepage_url:
            raise ConfigError("sources --action add requires --source-id and --homepage-url")
        created = scaffold_source_config(
            source_id,
            source_name=source_name or source_id.upper(),
            homepage_url=homepage_url,
            feed_url=feed_url or "",
            tier=tier or 3,
            country_region=country_region,
            language=language,
            topic_id=topic_id or settings.active_topic,
            out_dir=settings.sources_dir,
        )
        print(f"Created source config: {created}")
        print("Next: complete T0 rights review, then run `mq sources --action sync-csv`.")
        return EXIT_OK

    if action == "sync-csv":
        sources_n, routes_n = sync_sources_to_csv(
            settings.sources_dir,
            sources_csv=settings.sources_csv,
            routes_csv=settings.source_topics_csv,
        )
        print(
            f"Synced {sources_n} source(s) to {settings.sources_csv} and "
            f"{routes_n} route(s) to {settings.source_topics_csv}."
        )
        return EXIT_OK

    rows = _load_registry(settings)
    report_date = settings.report_date
    report = _read_json(settings.audit_dir / f"audit-{report_date.isoformat()}.json")
    coverage = {
        str(item.get("source_id")): item
        for item in cast(list[dict[str, Any]], report.get("coverage", []))
    }
    printed = 0
    for row in rows:
        if tier is not None and row.tier != tier:
            continue
        if rights_status is not None and row.rights_status != rights_status:
            continue
        printed += 1
        cov = coverage.get(row.source_id, {})
        gate = "usable" if row.usable else "blocked"
        print(
            f"{row.source_id:<16} tier={row.tier} rights={row.rights_status:<20} "
            f"access={row.access_mode:<20} enabled={row.enabled!s:<5} gate={gate:<7} "
            f"coverage={cov.get('status', 'not-run')} quotes={cov.get('quotes', 0)}"
        )
    if not printed:
        print("No sources match the filters.")
    return EXIT_OK


def _do_topics(
    settings: Settings,
    *,
    action: str = "list",
    topic_id: str | None = None,
    name: str = "",
    description: str = "",
) -> int:  # pragma: no cover
    if action == "init":
        if not topic_id:
            raise ConfigError("topics --action init requires --topic <topic_id>")
        created = scaffold_topic_pack(
            topic_id,
            name=name,
            description=description,
            out_dir=settings.topics_dir,
        )
        print(f"Created topic pack: {created}")
        return EXIT_OK

    if action == "show":
        target = topic_id or settings.active_topic
        try:
            pack = load_topic_pack(target, settings.topics_dir)
        except (FileNotFoundError, ValueError) as exc:
            raise ConfigError(str(exc)) from exc
        print(json.dumps(pack.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return EXIT_OK

    packs = list_topic_packs(settings.topics_dir)
    if not packs:
        print(f"No topic packs found in {settings.topics_dir}.")
        return EXIT_OK
    for pack in packs:
        marker = "*" if pack.topic_id == settings.active_topic else " "
        sections = ", ".join(pack.section_ids())
        print(
            f"{marker} {pack.topic_id:<18} v{pack.version:<6} "
            f"rule={pack.relevance.rule_id:<18} sections=[{sections}]"
        )
    return EXIT_OK


def _do_digest(
    settings: Settings,
    *,
    report_date: date,
    out: str | None,
    topic_id: str | None = None,
) -> int:  # pragma: no cover
    active_topic_id = str(
        topic_id or getattr(settings, "active_topic", DEFAULT_TOPIC_ID) or DEFAULT_TOPIC_ID
    )
    topic_pack = _load_topic_pack(settings, active_topic_id, strict=topic_id is not None)
    records = read_jsonl(
        local_store_path(settings.export_dir, report_date, topic_id=active_topic_id)
    )
    if not records:
        print(
            f"no verified local quote store for {report_date}; "
            f"run `mq run --date {report_date}` first",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    path = write_digest(
        records,
        report_date=report_date,
        window_start=_window(report_date, settings)[0],
        window_end=report_date,
        out_path=Path(out)
        if out
        else _digest_path(settings, report_date, topic_id=active_topic_id),
        sections_csv=settings.sections_csv,
        topic_pack=topic_pack,
        provider=make_provider(settings, topic_pack=topic_pack),
        notice=_NOTICE,
    )
    print(f"Digest {len(records)} quote(s): {path}")
    return EXIT_OK


def _do_export(
    settings: Settings,
    *,
    report_date: date,
    out: str | None,
    topic_id: str | None = None,
    from_db: bool = False,
) -> int:  # pragma: no cover
    active_topic_id = str(
        topic_id or getattr(settings, "active_topic", DEFAULT_TOPIC_ID) or DEFAULT_TOPIC_ID
    )
    if from_db:
        from ..db.repository import read_export_records
        from ..pipeline import _default_session_context

        with _default_session_context(settings) as session:
            records = read_export_records(
                session,
                report_date=report_date,
                window_days=settings.report_window_days,
                topic_id=active_topic_id,
            )
    else:
        records = read_jsonl(
            local_store_path(settings.export_dir, report_date, topic_id=active_topic_id)
        )
    if not records:
        print(
            "no verified local quote store; run `mq run --date ...` first",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    default_export = (
        settings.export_dir / active_topic_id / "quotes.jsonl"
        if active_topic_id != DEFAULT_TOPIC_ID
        else settings.export_dir / "quotes.jsonl"
    )
    path = Path(out) if out else default_export
    count = write_jsonl(records, path)
    print(f"Exported {count} schema-valid quote(s) to {path}")
    return EXIT_OK


def _do_dashboard(settings: Settings) -> int:  # pragma: no cover
    installed = importlib.util.find_spec("streamlit") is not None
    message = (
        "Dashboard not launched in blocked mode; run `streamlit run src/media_quotes/dashboard.py`."
    )
    if not installed:
        message += " Install the `dashboard` extra first."
    print(message)
    print(f"Digests: {settings.digest_dir}; logs: {settings.logs_dir}; audit: {settings.audit_dir}")
    return EXIT_OK
