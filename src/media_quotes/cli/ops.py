"""Operational CLI commands: backfill estimates, sources, digests, exports, dashboard."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

from ..config import Settings
from ..digest import write_digest
from ..exceptions import ConfigError
from ..export import local_store_path, read_jsonl, write_jsonl
from ..llm import make_provider
from ..risk_controls import budget_gate, estimate_backfill
from .common import (
    _ARTICLES_PER_HOUR,
    _ARTICLES_PER_SOURCE_DAY,
    _CALLS_PER_ARTICLE,
    _MAX_BACKFILL_DAYS,
    _NOTICE,
    _PRICE_PER_CALL,
    EXIT_FAILURE,
    EXIT_OK,
    _load_registry,
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
        f"Backfill {start}..{end} (bounded to {span} day(s)): "
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
    return EXIT_OK


def _do_sources(
    settings: Settings, *, tier: int | None, rights_status: str | None
) -> int:  # pragma: no cover
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


def _do_digest(
    settings: Settings, *, report_date: date, out: str | None
) -> int:  # pragma: no cover
    records = read_jsonl(local_store_path(settings.export_dir, report_date))
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
        out_path=Path(out) if out else settings.digest_dir / f"{report_date.isoformat()}.md",
        sections_csv=settings.sections_csv,
        provider=make_provider(settings),
        notice=_NOTICE,
    )
    print(f"Digest {len(records)} quote(s): {path}")
    return EXIT_OK


def _do_export(
    settings: Settings, *, report_date: date, out: str | None
) -> int:  # pragma: no cover
    records = read_jsonl(local_store_path(settings.export_dir, report_date))
    if not records:
        print(
            "no verified local quote store; run `mq run --date ...` first",
            file=sys.stderr,
        )
        return EXIT_FAILURE
    path = Path(out) if out else settings.export_dir / "quotes.jsonl"
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
