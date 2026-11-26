"""Deterministic audit report generation for offline runs."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, cast

from ..config import DEFAULT_TOPIC_ID, Settings
from ..enums import AcquisitionProvenance
from ..processing.audit import (
    _audit_markdown,
    _audit_report,
    _audit_target_dir,
    _write_audit,
)
from ..processing.export import local_store_path, read_jsonl
from ..text_utils import require_safe_id
from .common import (
    EXIT_FAILURE,
    _gap_record,
    _load_registry,
    _read_json,
    _run_id,
    exit_code_for,
    load_review_flags,
)

# Compatibility re-exports: definitions now live in processing.audit.
__all__ = [
    "_audit_markdown",
    "_audit_report",
    "_audit_target_dir",
    "_do_audit",
    "_write_audit",
]


def _do_audit(  # pragma: no cover
    settings: Settings,
    *,
    report_date: date,
    run_id: str | None,
    out: str | None,
    topic_id: str | None = None,
    provenance: AcquisitionProvenance = "fixture",
) -> int:
    active_topic = str(topic_id or getattr(settings, "active_topic", DEFAULT_TOPIC_ID))
    require_safe_id(active_topic, "topic_id")
    if run_id:
        require_safe_id(run_id, "run_id")
    day = report_date.isoformat()
    stored = _read_json(_audit_target_dir(settings, active_topic, provenance) / f"audit-{day}.json")
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
        if settings.persist_to_postgres:
            from ..db.repository import read_export_records
            from ..pipeline import default_session_context

            with default_session_context(settings) as session:
                records = read_export_records(
                    session,
                    report_date=report_date,
                    window_days=settings.report_window_days,
                    topic_id=active_topic,
                    provenance=provenance,
                )
        else:
            records = read_jsonl(
                local_store_path(
                    settings.export_dir, report_date, topic_id=active_topic, provenance=provenance
                )
            )
        audit_run_id = run_id or _run_id(
            report_date, settings, topic_id=active_topic, provenance=provenance
        )
        report = _audit_report(
            settings,
            report_date=report_date,
            run_id=audit_run_id,
            records=records,
            coverage=[],
            gaps=[_gap_record(row) for row in _load_registry(settings) if not row.usable],
            events=[],
            rejected=0,
            review_queue=[
                flag.model_dump(mode="json") for flag in load_review_flags(settings, provenance)
            ],
            topic_id=active_topic,
            provenance=provenance,
        )
        report["reconstructed"] = True
    out_dir = Path(out) if out else None
    json_path, md_path = _write_audit(settings, report, out_dir, provenance)
    checks = cast(dict[str, Any], report["checks"])
    print(
        f"Audit {day}: gates_pass={checks['gates_pass']}, "
        f"gaps={len(report['coverage_gaps'])}, quotes={report['verified_quotes']}"
    )
    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    return exit_code_for(ok=bool(checks["gates_pass"]), gaps=report["coverage_gaps"])
