"""Deterministic audit report generation for offline runs."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, cast

from ..config import DEFAULT_TOPIC_ID, Settings
from ..export import atomic_write_text, local_store_path, read_jsonl
from ..models import ExportQuoteRecord
from ..text_utils import require_safe_id
from .common import (
    EXIT_FAILURE,
    _digest_path,
    _gap_record,
    _load_registry,
    _read_json,
    _run_id,
    _version_manifest,
    _window,
    _write_json,
    exit_code_for,
)


def _audit_report(  # pragma: no cover
    settings: Settings,
    *,
    report_date: date,
    run_id: str,
    records: Sequence[ExportQuoteRecord],
    coverage: Sequence[dict[str, Any]],
    gaps: Sequence[dict[str, Any]],
    events: Sequence[dict[str, Any]],
    rejected: int,
    review_queue: Sequence[dict[str, Any]] = (),
    topic_id: str = DEFAULT_TOPIC_ID,
    source_mode: str = "live",
    should_persist: bool | None = None,
) -> dict[str, Any]:
    window_start, window_end = _window(report_date, settings)
    persist = settings.persist_to_postgres if should_persist is None else should_persist
    checks = {
        "config_valid": settings.sources_csv.is_file() and settings.sections_csv.is_file(),
        "registry_t0_gate_approved": all(
            not row.enabled or row.usable for row in _load_registry(settings)
        ),
        "coverage_gaps_disclosed": bool(gaps),
        "records_nonempty": bool(records),
        "schema_valid": all(isinstance(record.quote_id, str) for record in records),
        "verification_gates_passed": all(record.verification.verbatim_match for record in records),
        "no_unverified_quotes_published": all(
            record.verification.verbatim_match and record.verification.metadata_verified
            for record in records
        ),
        "trace_complete": all(bool(event.get("trace_id")) for event in events),
        "license_reference_logged": all("license_reference" in event for event in events),
        "no_database_writes": not persist,
        "no_llm_api_calls": settings.llm_provider.strip().lower() == "local",
    }
    # Coverage gaps are expected while sources are T0-blocked. Database writes are
    # allowed when PERSIST_TO_POSTGRES=true (the mode used for production verification);
    # in that mode `no_database_writes` stays informational rather than a gate.
    # Similarly, when live LLM provider is explicitly enabled (e.g. OpenRouter),
    # `no_llm_api_calls` stays informational rather than failing gates_pass.
    informational = {"coverage_gaps_disclosed"}
    if persist:
        informational.add("no_database_writes")
    if settings.llm_provider.strip().lower() == "openrouter":
        informational.add("no_llm_api_calls")
    checks["gates_pass"] = all(value for key, value in checks.items() if key not in informational)
    return {
        "topic_id": topic_id,
        "report_date": report_date.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "run_id": run_id,
        "mode": (
            "synthetic fixture replay (test output)"
            if source_mode == "fixtures"
            else "live Postgres + OpenRouter"
            if persist and settings.llm_provider == "openrouter"
            else "Postgres persistent"
            if persist
            else "live/blob file output"
        ),
        "verified_quotes": len(records),
        "rejected_candidates": rejected,
        "coverage": list(coverage),
        "coverage_gaps": list(gaps),
        "review_queue": list(review_queue),
        "checks": checks,
        "version_manifest": _version_manifest(
            settings, topic_id=topic_id, fixture_replay=source_mode == "fixtures"
        ),
        "artifacts": {
            "digest": str(_digest_path(settings, report_date, topic_id=topic_id)),
            "quotes": str(local_store_path(settings.export_dir, report_date, topic_id=topic_id)),
            "logs": str(settings.logs_dir / f"{run_id}.jsonl"),
        },
        "events": list(events),
    }


def _audit_markdown(report: dict[str, Any]) -> str:  # pragma: no cover
    checks = cast(dict[str, Any], report["checks"])
    lines = [
        f"# Audit report — {report['report_date']}",
        "",
        f"- Run ID: `{report['run_id']}`",
        f"- Mode: {report['mode']}",
        f"- Verified quotes: {report['verified_quotes']}",
        f"- Rejected candidates: {report['rejected_candidates']}",
        f"- Gates pass: {checks['gates_pass']}",
        "",
        "## Deterministic checks",
        "",
    ]
    lines.extend(f"- `{name}`: {value}" for name, value in sorted(checks.items()))
    lines.extend(["", "## Coverage gaps", ""])
    gaps = cast(list[dict[str, Any]], report["coverage_gaps"])
    if gaps:
        lines.append("| Source ID | Status | Reason |")
        lines.append("|---|---|---|")
        for gap in gaps:
            lines.append(f"| {gap['source_id']} | {gap['status']} | {gap['gap_reason']} |")
    else:
        lines.append("_No coverage gaps._")
    lines.extend(["", "## Artifacts", ""])
    for key, path in sorted(report["artifacts"].items()):
        lines.append(f"- {key}: `{path}`")
    return "\n".join(lines) + "\n"


def _audit_target_dir(settings: Settings, topic_id: str | None = None) -> Path:
    if topic_id and topic_id != DEFAULT_TOPIC_ID:
        target = settings.audit_dir / require_safe_id(topic_id, "topic_id")
    else:
        target = settings.audit_dir
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_audit(
    settings: Settings,
    report: dict[str, Any],
    out_dir: Path | None = None,
) -> tuple[Path, Path]:
    topic_id = str(report.get("topic_id", DEFAULT_TOPIC_ID))
    day = report["report_date"]
    if out_dir is not None and out_dir.suffix == ".json":
        json_path = out_dir
        md_path = out_dir.with_suffix(".md")
    else:
        target_dir = out_dir or _audit_target_dir(settings, topic_id)
        json_path = target_dir / f"audit-{day}.json"
        md_path = target_dir / f"audit-{day}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(json_path, report)
    atomic_write_text(md_path, _audit_markdown(report))
    return json_path, md_path


def _do_audit(  # pragma: no cover
    settings: Settings,
    *,
    report_date: date,
    run_id: str | None,
    out: str | None,
    topic_id: str | None = None,
    source_mode: str = "live",
) -> int:
    active_topic = str(topic_id or getattr(settings, "active_topic", DEFAULT_TOPIC_ID))
    require_safe_id(active_topic, "topic_id")
    if run_id:
        require_safe_id(run_id, "run_id")
    day = report_date.isoformat()
    stored = _read_json(_audit_target_dir(settings, active_topic) / f"audit-{day}.json")
    if run_id:
        if not stored:
            print(
                f"no stored audit report found on {day} for run_id={run_id}",
                file=sys.stderr,
            )
            return EXIT_FAILURE
        if stored.get("run_id") != run_id:
            print(
                f"no audit report for run_id={run_id} on {day} (stored run={stored.get('run_id')})",
                file=sys.stderr,
            )
            return EXIT_FAILURE
    if stored:
        if str(stored.get("topic_id", DEFAULT_TOPIC_ID)) != active_topic:
            print(
                f"no audit report for topic={active_topic} on {day} "
                f"(stored topic={stored.get('topic_id', DEFAULT_TOPIC_ID)})",
                file=sys.stderr,
            )
            return EXIT_FAILURE
        report: dict[str, Any] = stored
    else:
        if settings.persist_to_postgres and source_mode != "fixtures":
            from ..db.repository import read_export_records
            from ..pipeline import _default_session_context

            with _default_session_context(settings) as session:
                records = read_export_records(
                    session,
                    report_date=report_date,
                    window_days=settings.report_window_days,
                    topic_id=active_topic,
                )
        else:
            records = read_jsonl(
                local_store_path(settings.export_dir, report_date, topic_id=active_topic)
            )
        audit_run_id = run_id or _run_id(report_date, settings, topic_id=active_topic)
        from .common import load_review_flags

        report = _audit_report(
            settings,
            report_date=report_date,
            run_id=audit_run_id,
            records=records,
            coverage=[],
            gaps=[_gap_record(row) for row in _load_registry(settings) if not row.usable],
            events=[],
            rejected=0,
            review_queue=[flag.model_dump(mode="json") for flag in load_review_flags(settings)],
            topic_id=active_topic,
            source_mode=source_mode,
            should_persist=source_mode != "fixtures" and settings.persist_to_postgres,
        )
    out_dir = Path(out) if out else None
    json_path, md_path = _write_audit(settings, report, out_dir)
    checks = cast(dict[str, Any], report["checks"])
    print(
        f"Audit {day}: gates_pass={checks['gates_pass']}, "
        f"gaps={len(report['coverage_gaps'])}, quotes={report['verified_quotes']}"
    )
    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    return exit_code_for(ok=bool(checks["gates_pass"]), gaps=report["coverage_gaps"])
