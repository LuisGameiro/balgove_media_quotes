"""Deterministic audit report generation shared by the pipeline and the CLI.

shortcut: helper functions still live in ``cli.common``; the CLI keeps thin
compatibility re-exports there and no caller outside the CLI depends on them.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any, cast

from ..cli import common as _common
from ..config import DEFAULT_TOPIC_ID, Settings
from ..enums import AcquisitionProvenance
from ..models import ExportQuoteRecord
from .export import atomic_write_text, local_store_path
from .window import window


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
    provenance: AcquisitionProvenance = "fixture",
    source_mode: str = "fixtures",
    scope: str | None = None,
) -> dict[str, Any]:
    window_start, window_end = window(report_date, settings)
    try:
        registry_rows = _common._load_registry(settings)
        registry_readable = True
    except (OSError, ValueError):
        registry_rows = []
        registry_readable = False
    checks = {
        "config_valid": settings.sources_csv.is_file() and settings.sections_csv.is_file(),
        "registry_t0_gate_approved": registry_readable
        and all(not row.enabled or row.usable for row in registry_rows),
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
    informational = {"coverage_gaps_disclosed"}
    if settings.persist_to_postgres:
        informational.add("no_database_writes")
    if settings.llm_provider.strip().lower() == "openrouter":
        informational.add("no_llm_api_calls")
    checks["gates_pass"] = all(value for key, value in checks.items() if key not in informational)
    return {
        "topic_id": topic_id,
        "source_mode": source_mode,
        "provenance": provenance,
        "artifact_scope": scope,
        "report_date": report_date.isoformat(),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "run_id": run_id,
        "mode": (
            "live collection"
            if provenance != "fixture"
            else "live Postgres + OpenRouter"
            if settings.persist_to_postgres and settings.llm_provider == "openrouter"
            else "Postgres persistent"
            if settings.persist_to_postgres
            else "blocked/degraded (synthetic fixture replay)"
        ),
        "verified_quotes": len(records),
        "rejected_candidates": rejected,
        "coverage": list(coverage),
        "coverage_gaps": list(gaps),
        "review_queue": list(review_queue),
        "checks": checks,
        "version_manifest": _common._version_manifest(
            settings, topic_id=topic_id, source_mode=source_mode
        ),
        "artifacts": {
            "digest": str(
                _common._digest_path(
                    settings, report_date, topic_id=topic_id, provenance=provenance, scope=scope
                )
            ),
            "quotes": str(
                local_store_path(
                    settings.export_dir,
                    report_date,
                    topic_id=topic_id,
                    provenance=provenance,
                    scope=scope,
                )
            ),
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
    if report.get("reconstructed"):
        lines.insert(4, "- Reconstructed: true (no stored report; rebuilt from local artifacts)")
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


def _audit_target_dir(
    settings: Settings,
    topic_id: str | None = None,
    provenance: AcquisitionProvenance = "fixture",
    scope: str | None = None,
) -> Path:
    from ..text_utils import require_safe_id

    if provenance == "fixture":
        root = settings.audit_dir / "fixtures"
    elif scope:
        root = settings.audit_dir / "runs" / scope
    else:
        root = settings.audit_dir
    if topic_id and topic_id != DEFAULT_TOPIC_ID:
        target = root / require_safe_id(topic_id, "topic_id")
    else:
        target = root
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_audit(
    settings: Settings,
    report: dict[str, Any],
    out_dir: Path | None = None,
    provenance: AcquisitionProvenance = "fixture",
    scope: str | None = None,
) -> tuple[Path, Path]:
    topic_id = str(report.get("topic_id", DEFAULT_TOPIC_ID))
    day = report["report_date"]
    if out_dir is not None and out_dir.suffix == ".json":
        json_path = out_dir
        md_path = out_dir.with_suffix(".md")
    else:
        target_dir = out_dir or _audit_target_dir(settings, topic_id, provenance, scope)
        json_path = target_dir / f"audit-{day}.json"
        md_path = target_dir / f"audit-{day}.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    _common._write_json(json_path, report)
    atomic_write_text(md_path, _audit_markdown(report))
    return json_path, md_path
