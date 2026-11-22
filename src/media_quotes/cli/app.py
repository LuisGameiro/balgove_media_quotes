"""CLI parser, validation/bootstrap helpers and command dispatch."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from ..exceptions import BudgetExceededError, ConfigError, MediaQuotesError, ValidationError
from ..validation import validate_data_dir
from .common import (
    _MANDATORY,
    DATA_DIR,
    EXIT_CONFIG,
    EXIT_FAILURE,
    EXIT_OK,
    _load_registry,
    _translation_readiness_warning,
    _version_manifest,
    render_errors,
)


def _validate() -> int:
    return render_errors(validate_data_dir(DATA_DIR))


def _bootstrap() -> int:
    from ..bootstrap import main as bootstrap_main

    bootstrap_main()
    return 0


def _do_doctor(settings: Settings) -> int:  # pragma: no cover
    # Resolve through the package so callers can replace cli._validate at runtime.
    from . import _validate as validate

    result = validate()
    if result != EXIT_OK:
        return result
    rows = _load_registry(settings)
    blocked = sum(1 for row in rows if row.source_id in _MANDATORY and not row.usable)
    dagster_home = os.environ.get("DAGSTER_HOME", str(settings.audit_dir.parent / "dagster"))
    print(f"T0 gate: {blocked} mandatory source(s) blocked; fixture replay available")
    print(
        f"Config: report_date={settings.report_date} window={settings.report_window_days}d "
        f"topic={settings.active_topic} llm_provider={settings.llm_provider} "
        f"persist_to_postgres={settings.persist_to_postgres} dagster_home={dagster_home}"
    )
    print(f"Version manifest: {json.dumps(_version_manifest(settings), sort_keys=True)}")
    warning = _translation_readiness_warning(settings)
    if warning:
        print(f"Warning: {warning}")
    return EXIT_OK


def _add_shared(parser: argparse.ArgumentParser) -> None:  # pragma: no cover
    parser.add_argument(
        "--offline",
        action="store_true",
        help="deprecated, accepted for compatibility: all mq commands are offline",
    )
    parser.add_argument("--dry-run", action="store_true", help="plan only; do not write")
    parser.add_argument("--date", dest="date", help="report date YYYY-MM-DD")
    parser.add_argument("--topic", dest="topic", help="topic pack id from data/topics/")
    parser.add_argument("--run-id", dest="run_id")
    parser.add_argument("--trace-id", dest="trace_id")
    parser.add_argument("--out", dest="out", help="output path or directory")
    parser.add_argument("--verbose", action="store_true", help="print extra detail")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mq", description="Media quotes utilities (offline)")
    subparsers = parser.add_subparsers(dest="command", required=True)
    shared: dict[str, Any] = {"parents": [_shared_parser()]}

    subparsers.add_parser("doctor", help="validate config, registry and schema", **shared)
    subparsers.add_parser("validate", help="alias for doctor", **shared)
    subparsers.add_parser("bootstrap", help="initialise database and sync sources", **shared)
    subparsers.add_parser("run", help="offline fixture replay pipeline", **shared)
    fetch_parser = subparsers.add_parser(
        "fetch", help="fetch a rights-cleared source feed and articles"
    )
    fetch_parser.add_argument("--date", dest="date", help="report date YYYY-MM-DD")
    fetch_parser.add_argument("--topic", dest="topic", help="topic pack id from data/topics/")
    fetch_parser.add_argument("--source-id", dest="fetch_source_id", required=True)
    fetch_parser.add_argument("--limit", dest="limit", type=int, default=2)
    fetch_parser.add_argument(
        "--live", action="store_true", help="actually fetch; without it the command only plans"
    )
    subparsers.add_parser("audit", help="deterministic audit report (JSON + MD)", **shared)
    subparsers.add_parser("logs", help="correlated JSON logs for a run", **shared)
    eval_parser = subparsers.add_parser("eval", help="metrics on a synthetic gold set", **shared)
    eval_parser.add_argument("--gold-set", dest="gold_set", help="gold set version")
    eval_parser.add_argument(
        "--split",
        dest="split",
        choices=("all", "train", "dev", "test"),
        default=None,
        help="optional split filter (all, train, dev, test)",
    )
    sources = subparsers.add_parser("sources", help="registry and T0 state at a glance", **shared)
    sources.add_argument(
        "--action",
        choices=("list", "add", "sync-csv"),
        default="list",
        help="list sources, scaffold a new data/sources/<id>.json, or sync CSV contracts",
    )
    sources.add_argument("--tier", dest="tier", type=int, choices=(1, 2, 3, 4))
    sources.add_argument("--rights-status", dest="rights_status")
    sources.add_argument("--source-id", dest="source_id", help="source id for --action add")
    sources.add_argument("--name", dest="source_name", help="display name for --action add")
    sources.add_argument(
        "--homepage-url", dest="homepage_url", help="homepage URL for --action add"
    )
    sources.add_argument("--feed-url", dest="feed_url", help="discovery RSS/sitemap URL")
    sources.add_argument(
        "--language", dest="language", default="en", help="ISO 639-1 language code"
    )
    sources.add_argument("--country-region", dest="country_region", default="Global")
    topics = subparsers.add_parser("topics", help="list, inspect or scaffold topic packs", **shared)
    topics.add_argument(
        "--action",
        choices=("list", "init", "show"),
        default="list",
        help="list available topic packs, scaffold a new one, or print one as JSON",
    )
    topics.add_argument(
        "--name", dest="topic_name", default="", help="topic name for --action init"
    )
    topics.add_argument(
        "--description", dest="topic_description", default="", help="topic description"
    )
    subparsers.add_parser("version", help="version manifest", **shared)
    backfill = subparsers.add_parser("backfill", help="bounded offline backfill estimate", **shared)
    backfill.add_argument("--from", dest="from_date", help="period start YYYY-MM-DD")
    backfill.add_argument("--to", dest="to_date", help="period end YYYY-MM-DD")
    backfill.add_argument("--max-cost", dest="max_cost", type=float)
    subparsers.add_parser("digest", help="regenerate the Markdown digest", **shared)
    subparsers.add_parser("export", help="derived JSONL export", **shared)
    subparsers.add_parser("dashboard", help="Streamlit dashboard guidance", **shared)
    subparsers.add_parser("dagster", help="Dagster asset/schedule guidance", **shared)
    review = subparsers.add_parser("review", help="obsolescence review queue", **shared)
    review.add_argument("--action", choices=("list", "decide", "seed"), default="list")
    review.add_argument("--quote-id", dest="quote_id")
    review.add_argument("--verdict", choices=("confirmed", "rejected", "escalated"), dest="verdict")
    review.add_argument("--reason-code", dest="reason_code", default="human-review")
    subparsers.add_parser("retention", help="retention and lock readiness plan", **shared)
    drill = subparsers.add_parser("restore-drill", help="compare two derived exports", **shared)
    drill.add_argument("--reference", dest="reference")
    return parser


def _shared_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    _add_shared(parser)
    return parser


def _ensure_utf8_output() -> None:
    """Windows consoles default to cp1252; quotes contain Cyrillic and em dashes."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def cli_entry(argv: Sequence[str] | None = None) -> int:
    """CLI-only entry point: fix the console encoding, then run main()."""
    _ensure_utf8_output()
    return main(argv)


def main(argv: Sequence[str] | None = None) -> int:
    # Look up commands through the package at call time so monkeypatched cli
    # attributes are honoured rather than stale app-module bindings.
    from . import (
        _bootstrap,
        _do_audit,
        _do_backfill,
        _do_dagster,
        _do_dashboard,
        _do_digest,
        _do_doctor,
        _do_eval,
        _do_export,
        _do_fetch,
        _do_logs,
        _do_restore_drill,
        _do_retention,
        _do_review,
        _do_run,
        _do_sources,
        _do_topics,
        _parse_report_date,
    )
    from . import _version_manifest as package_version_manifest

    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        command = str(args.command)
        topic_arg = getattr(args, "topic", None)
        if command in {"doctor", "validate"}:
            return _do_doctor(settings)
        if command == "bootstrap":
            return _bootstrap()
        if command == "version":
            print(
                json.dumps(
                    package_version_manifest(settings), ensure_ascii=False, sort_keys=True, indent=2
                )
            )
            return EXIT_OK
        if command == "dashboard":
            return _do_dashboard(settings)
        if command == "dagster":
            return _do_dagster(settings)
        if command == "review":
            return _do_review(
                settings,
                action=str(args.action),
                quote_id=getattr(args, "quote_id", None),
                verdict=getattr(args, "verdict", None),
                reason_code=str(getattr(args, "reason_code", "human-review")),
            )
        if command == "retention":
            return _do_retention(settings, dry_run=bool(args.dry_run))
        if command == "restore-drill":
            report_date = _parse_report_date(getattr(args, "date", None), settings)
            reference = getattr(args, "reference", None)
            return _do_restore_drill(
                settings,
                report_date=report_date,
                reference=Path(reference) if reference else None,
                topic_id=topic_arg,
            )
        if command == "sources":
            action = str(getattr(args, "action", "list"))
            if action == "list":
                return _do_sources(
                    settings,
                    tier=int(args.tier) if getattr(args, "tier", None) else None,
                    rights_status=getattr(args, "rights_status", None),
                )
            return _do_sources(
                settings,
                tier=int(args.tier) if getattr(args, "tier", None) else None,
                rights_status=getattr(args, "rights_status", None),
                action=action,
                source_id=getattr(args, "source_id", None),
                source_name=getattr(args, "source_name", None),
                homepage_url=getattr(args, "homepage_url", None),
                feed_url=getattr(args, "feed_url", None),
                language=str(getattr(args, "language", "en")),
                country_region=str(getattr(args, "country_region", "Global")),
                topic_id=topic_arg,
            )
        if command == "topics":
            return _do_topics(
                settings,
                action=str(getattr(args, "action", "list")),
                topic_id=topic_arg,
                name=str(getattr(args, "topic_name", "")),
                description=str(getattr(args, "topic_description", "")),
            )
        if command == "fetch":
            report_date = _parse_report_date(getattr(args, "date", None), settings)
            return _do_fetch(
                settings,
                source_id=str(args.fetch_source_id),
                report_date=report_date,
                limit=int(args.limit),
                dry_run=not bool(args.live),
                topic_id=topic_arg,
            )
        if command in {"run", "audit", "digest"}:
            report_date = _parse_report_date(getattr(args, "date", None), settings)
            if command == "run":
                if topic_arg is not None:
                    return _do_run(
                        settings,
                        report_date=report_date,
                        dry_run=args.dry_run,
                        run_id=getattr(args, "run_id", None),
                        verbose=args.verbose,
                        topic_id=topic_arg,
                    )
                return _do_run(
                    settings,
                    report_date=report_date,
                    dry_run=args.dry_run,
                    run_id=getattr(args, "run_id", None),
                    verbose=args.verbose,
                )
            if command == "audit":
                return _do_audit(
                    settings,
                    report_date=report_date,
                    run_id=getattr(args, "run_id", None),
                    out=getattr(args, "out", None),
                    topic_id=topic_arg,
                )
            if topic_arg is not None:
                return _do_digest(
                    settings,
                    report_date=report_date,
                    out=getattr(args, "out", None),
                    topic_id=topic_arg,
                )
            return _do_digest(settings, report_date=report_date, out=getattr(args, "out", None))
        if command == "logs":
            run_id = getattr(args, "run_id", None)
            if not run_id:
                raise ConfigError("logs requires --run-id")
            return _do_logs(settings, run_id=run_id, trace_id=getattr(args, "trace_id", None))
        if command == "eval":
            gold_set = getattr(args, "gold_set", None) or settings.gold_set_version
            report_date = _parse_report_date(getattr(args, "date", None), settings)
            split = getattr(args, "split", None)
            if split is not None:
                return _do_eval(settings, gold_set=gold_set, report_date=report_date, split=split)
            return _do_eval(settings, gold_set=gold_set, report_date=report_date)
        if command == "export":
            report_date = _parse_report_date(getattr(args, "date", None), settings)
            if topic_arg is not None:
                return _do_export(
                    settings,
                    report_date=report_date,
                    out=getattr(args, "out", None),
                    topic_id=topic_arg,
                )
            return _do_export(settings, report_date=report_date, out=getattr(args, "out", None))
        if command == "backfill":
            start = (
                _parse_report_date(args.from_date, settings)
                if getattr(args, "from_date", None)
                else settings.backfill_start
            )
            end = (
                _parse_report_date(args.to_date, settings)
                if getattr(args, "to_date", None)
                else settings.report_date
            )
            return _do_backfill(
                settings,
                start=start,
                end=end,
                dry_run=args.dry_run,
                max_cost=getattr(args, "max_cost", None),
            )
    except (ValidationError, MediaQuotesError, FileExistsError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return (
            EXIT_CONFIG
            if isinstance(exc, (ConfigError, BudgetExceededError, ValueError))
            else EXIT_FAILURE
        )
    parser.error(f"unknown command: {args.command}")
    return EXIT_FAILURE
