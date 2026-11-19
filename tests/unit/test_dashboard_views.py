"""E2 — dashboard coverage and review views read the latest audit report."""

from __future__ import annotations

import json
from pathlib import Path

from media_quotes.dashboard import coverage_summary, review_queue_view


def write_audit(
    audit_dir: Path, payload: dict[str, object], day: str = "audit-2026-11-19.json"
) -> None:
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / day).write_text(json.dumps(payload), encoding="utf-8")


def test_views_return_empty_when_no_audit(tmp_path: Path) -> None:
    assert coverage_summary(tmp_path) == []
    assert review_queue_view(tmp_path) == []


def test_views_read_latest_report(tmp_path: Path) -> None:
    write_audit(
        tmp_path,
        {
            "coverage": [{"source_id": "reuters", "status": "blocked"}],
            "review_queue": [{"quote_id": "Q"}],
        },
        day="audit-2026-11-18.json",
    )
    write_audit(
        tmp_path,
        {
            "coverage": [{"source_id": "bbc", "status": "blocked"}],
            "review_queue": [{"quote_id": "Q2", "state": "pending"}],
        },
        day="audit-2026-11-19.json",
    )
    assert coverage_summary(tmp_path) == [{"source_id": "bbc", "status": "blocked"}]
    assert review_queue_view(tmp_path) == [{"quote_id": "Q2", "state": "pending"}]


def test_audit_report_includes_review_queue_key(tmp_path: Path) -> None:
    from media_quotes.cli.audit import _audit_report
    from media_quotes.cli.common import _parse_report_date
    from media_quotes.config import Settings

    settings = Settings(_env_file=None)
    report = _audit_report(
        settings,
        report_date=_parse_report_date(None, settings),
        run_id="r",
        records=[],
        coverage=[],
        gaps=[],
        events=[],
        rejected=0,
    )
    assert report["review_queue"] == []
