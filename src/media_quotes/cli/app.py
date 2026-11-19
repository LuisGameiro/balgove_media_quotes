"""CLI parser, validation/bootstrap helpers and command dispatch."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
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
    print(f"T0 gate: {blocked} mandatory source(s) blocked; fixture replay available")
    print(f"Config: report_date={settings.report_date} window={settings.report_window_days}d")
    print(f"Version manifest: {json.dumps(_version_manifest(settings), sort_keys=True)}")
    return EXIT_OK


def _add_shared(parser: argparse.ArgumentParser) -> None:  # pragma: no cover
    parser.add_argument("--offline", action="store_true", help="replay fixtures; no network")
    parser.add_argument("--dry-run", action="store_true", help="plan only; do not write")
    parser.add_argument("--date", dest="date", help="report date YYYY-MM-DD")
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
    subparsers.add_parser("audit", help="deterministic audit report (JSON + MD)", **shared)
    subparsers.add_parser("logs", help="correlated JSON logs for a run", **shared)
    eval_parser = subparsers.add_parser("eval", help="metrics on a synthetic gold set", **shared)
    eval_parser.add_argument("--gold-set", dest="gold_set", help="gold set version")
    sources = subparsers.add_parser("sources", help="registry and T0 state at a glance", **shared)
    sources.add_argument("--tier", dest="tier", type=int, choices=(1, 2, 3, 4))
    sources.add_argument("--rights-status", dest="rights_status")
    subparsers.add_parser("version", help="version manifest", **shared)
    backfill = subparsers.add_parser("backfill", help="bounded offline backfill estimate", **shared)
    backfill.add_argument("--from", dest="from_date", help="period start YYYY-MM-DD")
    backfill.add_argument("--to", dest="to_date", help="period end YYYY-MM-DD")
    backfill.add_argument("--max-cost", dest="max_cost", type=float)
    subparsers.add_parser("digest", help="regenerate the Markdown digest", **shared)
    subparsers.add_parser("export", help="derived JSONL export", **shared)
    subparsers.add_parser("dashboard", help="Streamlit dashboard guidance", **shared)
    return parser


def _shared_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    _add_shared(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    # Look up commands through the package at call time so monkeypatched cli
    # attributes are honoured rather than stale app-module bindings.
    from . import (
        _bootstrap,
        _do_audit,
        _do_backfill,
        _do_dashboard,
        _do_digest,
        _do_doctor,
        _do_eval,
        _do_export,
        _do_logs,
        _do_run,
        _do_sources,
        _parse_report_date,
    )
    from . import _version_manifest as package_version_manifest

    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        command = str(args.command)
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
        if command == "sources":
            return _do_sources(
                settings,
                tier=int(args.tier) if getattr(args, "tier", None) else None,
                rights_status=getattr(args, "rights_status", None),
            )
        if command in {"run", "audit", "digest"}:
            report_date = _parse_report_date(getattr(args, "date", None), settings)
            if command == "run":
                if args.verbose:
                    print(f"Offline fixture replay for {report_date} (offline={args.offline})")
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
            return _do_eval(settings, gold_set=gold_set, report_date=report_date)
        if command == "export":
            report_date = _parse_report_date(getattr(args, "date", None), settings)
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
    except (ValidationError, MediaQuotesError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG if isinstance(exc, (ConfigError, BudgetExceededError)) else EXIT_FAILURE
    parser.error(f"unknown command: {args.command}")
    return EXIT_FAILURE
