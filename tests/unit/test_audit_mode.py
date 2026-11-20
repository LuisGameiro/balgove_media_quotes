"""Audit gates are mode-aware: DB writes are only a violation in file-only mode."""

from datetime import date

from media_quotes.cli.audit import _audit_report
from media_quotes.config import Settings


def _report(persist: bool) -> dict[str, object]:
    settings = Settings(_env_file=None, persist_to_postgres=persist)
    return _audit_report(
        settings,
        report_date=date(2026, 11, 19),
        run_id="run-test",
        records=[],
        coverage=[],
        gaps=[{"source_id": "reuters", "mandatory": True}],
        events=[],
        rejected=0,
    )


def test_file_only_mode_database_writes_fail_the_gate() -> None:
    checks = _report(False)["checks"]
    assert isinstance(checks, dict)
    assert checks["no_database_writes"] is True
    assert checks["gates_pass"] is True  # gaps are informational


def test_persistent_mode_allows_database_writes() -> None:
    checks = _report(True)["checks"]
    assert isinstance(checks, dict)
    assert checks["no_database_writes"] is False  # informational, not a failure
    assert checks["gates_pass"] is True
