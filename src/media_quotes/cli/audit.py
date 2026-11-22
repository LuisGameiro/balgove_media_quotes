"""Deterministic audit report generation for offline runs."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, cast

from ..config import DEFAULT_TOPIC_ID, Settings
from ..enums import APPROVED_RIGHTS
from ..export import atomic_write_text, local_store_path, read_jsonl
from ..models import ExportQuoteRecord
from .common import (
    EXIT_FAILURE,
    EXIT_GAPS,
    EXIT_OK,
    _gap_record,
    _load_registry,
    _read_json,
    _run_id,
    _version_manifest,
    _window,
    _write_json,
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
) -> dict[str, Any]:
    window_start, window_end = _window(report_date, settings)
    checks = {
        "config_valid": settings.sources_csv.is_file() and settings.sections_csv.is_file(),
        "registry_t0_gate_approved": all(
            not row.enabled or row.rights_status in APPROVED_RIGHTS
            for row in _load_registry(settings)
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
        "no_database_writes": not settings.persist_to_postgres,
        "no_llm_api_calls": settings.llm_provider.strip().lower() == "local",
    }
    # Coverage gaps are expected while sources are T0-blocked. Database writes are
    # allowed when PERSIST_TO_POSTGRES=true (the mode used for production verification);
    # in that mode `no_database_writes` stays informational rather than a gate.
    informational = {"coverage_gaps_disclosed"}
    if settings.persist_to_postgres:
        informational.add("no_database_writes")
    checks["gates_pass"] = all(value for key, value in checks.items() if key not in informational)
    return {
        "topic_id": topic_id,
        "report_date": report_date.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "run_id": run_id,
        "mode": "blocked/degraded (synthetic fixture replay)",
        "exit_code": EXIT_GAPS if gaps else EXIT_OK,
        "verified_quotes": len(records),
        "rejected_candidates": rejected,
        "coverage": list(coverage),
        "coverage_gaps": list(gaps),
        "review_queue": list(review_queue),
        "checks": checks,
        "version_manifest": _version_manifest(settings),
        "artifacts": {
            "digest": str(settings.digest_dir / f"{report_date.isoformat()}.md"),
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
        for gap in gaps:
            mandatory = " (mandatory)" if gap.get("mandatory") else ""
            lines.append(
                f"- {gap['source_id']}{mandatory}: {gap['gap_reason']} — {gap.get('detail', '')}"
            )
    else:
        lines.append("- None.")
    lines.extend(["", "## Version manifest", ""])
    manifest = cast(dict[str, Any], report["version_manifest"])
    lines.extend(f"- `{name}`: {value}" for name, value in sorted(manifest.items()))
    return "\n".join(lines) + "\n"


def _audit_target_dir(settings: Settings, topic_id: str) -> Path:
    """Non-default topics get their own audit folder, like digests and exports."""
    if topic_id and topic_id != DEFAULT_TOPIC_ID:
        return settings.audit_dir / topic_id
    return settings.audit_dir


def _write_audit(  # pragma: no cover
    settings: Settings, report: dict[str, Any], out_dir: Path | None = None
) -> tuple[Path, Path]:
    day = str(report["report_date"])
    if out_dir is not None and out_dir.suffix == ".json":
        json_path = out_dir
        md_path = out_dir.with_suffix(".md")
    else:
        target = out_dir or _audit_target_dir(
            settings, str(report.get("topic_id", DEFAULT_TOPIC_ID))
        )
        json_path = target / f"audit-{day}.json"
        md_path = target / f"audit-{day}.md"
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
) -> int:
    active_topic = str(topic_id or getattr(settings, "active_topic", DEFAULT_TOPIC_ID))
    day = report_date.isoformat()
    stored = _read_json(_audit_target_dir(settings, active_topic) / f"audit-{day}.json")
    if run_id and stored and stored.get("run_id") != run_id:
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
        records = read_jsonl(
            local_store_path(settings.export_dir, report_date, topic_id=active_topic)
        )
        audit_run_id = run_id or _run_id(report_date, settings, topic_id=active_topic)
        report = _audit_report(
            settings,
            report_date=report_date,
            run_id=audit_run_id,
            records=records,
            coverage=[],
            gaps=[_gap_record(row) for row in _load_registry(settings) if not row.usable],
            events=[],
            rejected=0,
            review_queue=[],
            topic_id=active_topic,
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
    return EXIT_OK if checks["gates_pass"] else EXIT_FAILURE
