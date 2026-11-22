"""Regression tests for the repository audit fixes."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import SecretStr

from media_quotes.cli.audit import _audit_report
from media_quotes.cli.common import _version_manifest
from media_quotes.config import Settings
from media_quotes.curation import dedupe_quotes
from media_quotes.export import write_jsonl
from media_quotes.models import ExportQuoteRecord, VerificationRecord
from media_quotes.rights_guard import apply_rights_limits

ROOT = Path(__file__).resolve().parents[2]
REPORT_DATE = date(2026, 11, 19)


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "sources_csv": ROOT / "data/sources.csv",
        "sources_dir": ROOT / "data/sources",
        "source_topics_csv": ROOT / "data/source_topic_routes.csv",
        "topic_rules_path": ROOT / "data/topic_rules.json",
        "topics_dir": ROOT / "data/topics",
        "sections_csv": ROOT / "data/sections.csv",
        "schema_path": ROOT / "data/quotes.schema.json",
        "audit_dir": tmp_path / "audit",
        "export_dir": tmp_path / "export",
        "persist_to_postgres": False,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _record(quote_id: str, day: date, author: str = "Jane Doe") -> ExportQuoteRecord:
    now = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    return ExportQuoteRecord(
        quote_id=quote_id,
        quote_text_original="An attributed sentence about the conflict.",
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
        publication_date=day,
        article_name="Article",
        article_url=f"https://www.theguardian.com/a/{quote_id}",
        extraction_date=now,
        section_id="other",
        is_obsolete=False,
        content_hash="a" * 64,
        verification=VerificationRecord(
            verbatim_match=True, metadata_verified=True, method="m", verified_at=now
        ),
        article_author=author,
    )


def test_audit_llm_check_reflects_provider(tmp_path: Path) -> None:
    local = _audit_report(
        _settings(tmp_path),
        report_date=REPORT_DATE,
        run_id="r",
        records=[],
        coverage=[],
        gaps=[],
        events=[],
        rejected=0,
    )
    assert local["checks"]["no_llm_api_calls"] is True

    live = _settings(
        tmp_path,
        llm_provider="openrouter",
        llm_model_snapshot="openai/gpt-4o-mini-2024-07-18",
        openrouter_api_key=SecretStr("test-key"),
    )
    report = _audit_report(
        live,
        report_date=REPORT_DATE,
        run_id="r",
        records=[],
        coverage=[],
        gaps=[],
        events=[],
        rejected=0,
    )
    assert report["checks"]["no_llm_api_calls"] is False
    assert report["checks"]["gates_pass"] is False


def test_audit_artifact_path_uses_topic(tmp_path: Path) -> None:
    report = _audit_report(
        _settings(tmp_path),
        report_date=REPORT_DATE,
        run_id="r",
        records=[],
        coverage=[],
        gaps=[],
        events=[],
        rejected=0,
        topic_id="ai-policy",
    )
    assert "ai-policy" in report["artifacts"]["quotes"]
    assert report["topic_id"] == "ai-policy"


def test_dedupe_uses_a_rolling_window(tmp_path: Path) -> None:
    first = _record("Q-20261101-0001", date(2026, 11, 1))
    second = _record("Q-20261108-0001", date(2026, 11, 8))
    assert len(dedupe_quotes([first, second], window_days=14)) == 1


def test_rights_guard_requires_a_named_author() -> None:
    record = _record("Q-20261119-0001", REPORT_DATE, author="")
    decision = apply_rights_limits([record], max_quote_words=30)
    assert decision.kept == []
    assert any("missing_attribution" in item["reason"] for item in decision.rejected)


def test_registry_hash_uses_json_when_present(tmp_path: Path) -> None:
    sources_dir = tmp_path / "sources"
    sources_dir.mkdir()
    target = sources_dir / "example.json"
    target.write_text("{}", encoding="utf-8")
    settings = _settings(tmp_path, sources_dir=sources_dir)
    first = _version_manifest(settings)["registry_sha256"]
    target.write_text('{"enabled": true}', encoding="utf-8")
    assert _version_manifest(settings)["registry_sha256"] != first


def test_persist_result_commits_once() -> None:
    from unittest.mock import MagicMock

    from media_quotes.pipeline import DailyPipelineResult, _persist_result

    session = MagicMock()
    context = MagicMock()
    context.return_value.__enter__.return_value = session
    result = DailyPipelineResult(
        run_id="r",
        report_date=REPORT_DATE,
        dry_run=False,
        records=(),
        coverage=(),
        gaps=(),
        events=(),
        rejected=0,
        rejected_total=0,
        status="succeeded",
    )
    _persist_result(result, _settings(Path(".")), pipeline="test", session_context=context)
    assert session.commit.call_count == 1


def test_digest_migration_replaces_global_unique() -> None:
    import importlib.util

    path = ROOT / "migrations/versions/0006_digest_topic_unique.py"
    spec = importlib.util.spec_from_file_location("migration_0006", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0006_digest_topic_unique"
    assert module.down_revision == "0005_topic_packs"
    assert any("DROP CONSTRAINT IF EXISTS digests_digest_date_key" in sql for sql in module.UPGRADE)
    assert any("UNIQUE (topic_id, digest_date)" in sql for sql in module.UPGRADE)


def test_write_jsonl_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "quotes.jsonl"
    assert write_jsonl([_record("Q-20261119-0001", REPORT_DATE)], path) == 1
    assert json.loads(path.read_text(encoding="utf-8").splitlines()[0])["quote_id"] == (
        "Q-20261119-0001"
    )
    assert not list(tmp_path.rglob("*.tmp"))
