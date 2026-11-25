"""Audit gates are mode-aware: DB writes are only a violation in file-only mode."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from media_quotes.cli.audit import _audit_report, _do_audit, _write_audit
from media_quotes.config import Settings
from media_quotes.export import local_store_path, write_jsonl
from media_quotes.models import ExportQuoteRecord, VerificationRecord


def _record() -> ExportQuoteRecord:
    now = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)
    return ExportQuoteRecord(
        quote_id="Q-20261119-0001",
        quote_text_original="A short attributed sentence used for this audit gate test.",
        source_id="guardian",
        source_name="Guardian",
        source_tier=1,
        source_country_region="UK",
        source_type="publicly funded",
        rights_status="license_held",
        access_mode="licensed_database",
        license_reference="LIC-1",
        discovery_method="licensed_database",
        claim_only=False,
        original_reporting_flag="first-hand",
        source_language="en",
        publication_date=date(2026, 11, 19),
        article_name="Article",
        article_url="https://www.theguardian.com/a/test",
        extraction_date=now,
        section_id="other",
        is_obsolete=False,
        content_hash="a" * 64,
        verification=VerificationRecord(
            verbatim_match=True, metadata_verified=True, method="m", verified_at=now
        ),
    )


def _report(persist: bool) -> dict[str, object]:
    settings = Settings(persist_to_postgres=persist)
    return _audit_report(
        settings,
        report_date=date(2026, 11, 19),
        run_id="run-test",
        records=[_record()],
        coverage=[],
        gaps=[
            {
                "source_id": "reuters",
                "mandatory": True,
                "status": "blocked",
                "gap_reason": "rights_blocked",
            }
        ],
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


def test_live_audit_is_not_labelled_fixture_replay() -> None:
    report = _report(False)
    assert report["mode"] == "live/blob file output"
    manifest = report["version_manifest"]
    assert isinstance(manifest, dict)
    assert manifest["fixture_replay"] is False

    fixture_report = _audit_report(
        Settings(_env_file=None),
        report_date=date(2026, 11, 19),
        run_id="run-test",
        records=[_record()],
        coverage=[],
        gaps=[],
        events=[],
        rejected=0,
        source_mode="fixtures",
    )
    assert fixture_report["mode"] == "synthetic fixture replay (test output)"
    assert fixture_report["version_manifest"]["fixture_replay"] is True


def test_fixture_audit_never_reads_postgres_without_stored_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden_session(*args: object, **kwargs: object) -> object:
        raise AssertionError("fixture audit must not open Postgres")

    monkeypatch.setattr("media_quotes.pipeline._default_session_context", forbidden_session)
    settings = Settings(
        _env_file=None,
        output_dir=tmp_path / "output",
        persist_to_postgres=True,
        llm_provider="local",
    ).for_source_mode("fixtures")
    report_date = date(2026, 11, 19)
    write_jsonl(
        [_record()],
        local_store_path(settings.export_dir, report_date, topic_id="ukraine-war"),
    )

    assert (
        _do_audit(
            settings,
            report_date=report_date,
            run_id=None,
            out=None,
            source_mode="fixtures",
        )
        == 0
    )
    report = json.loads(
        (settings.audit_dir / f"audit-{report_date.isoformat()}.json").read_text(encoding="utf-8")
    )
    assert report["mode"] == "synthetic fixture replay (test output)"
    assert report["verified_quotes"] == 1


def test_write_audit_accepts_json_file_path(tmp_path: Path) -> None:
    target = tmp_path / "custom.json"
    json_path, md_path = _write_audit(Settings(_env_file=None), _report(False), out_dir=target)
    assert json_path == target
    assert md_path == target.with_suffix(".md")
    assert json_path.is_file()
