"""Maintenance CLI — review queue, retention plan and restore drill (offline)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from media_quotes.cli.common import exit_code_for
from media_quotes.cli.maintenance import _do_restore_drill, _do_retention, _do_review
from media_quotes.config import Settings
from media_quotes.exceptions import ConfigError
from media_quotes.risk_controls.models import ObsolescenceEvidence, ObsolescenceFlag


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        digest_dir=tmp_path / "digests",
        blob_dir=tmp_path / "blobs",
        sources_csv=Path("data/sources.csv"),
        sections_csv=Path("data/sections.csv"),
        schema_path=Path("data/quotes.schema.json"),
        fixtures_dir=Path("data/fixtures"),
        gold_set_dir=Path("data/gold_set"),
        audit_dir=tmp_path / "audit",
        export_dir=tmp_path / "export",
        logs_dir=tmp_path / "logs",
        alembic_ini=Path("alembic.ini"),
    )


def test_malformed_review_flag_is_not_silently_dropped(tmp_path: Path) -> None:
    from media_quotes.cli.common import load_review_flags

    settings = settings_for(tmp_path)
    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    (settings.audit_dir / "review-queue.json").write_text(
        json.dumps({"flags": [{"not_a_flag": True}]}), encoding="utf-8"
    )
    with pytest.raises(ValueError):
        load_review_flags(settings)


def test_queue_lock_is_released(tmp_path: Path) -> None:
    from media_quotes.cli.maintenance import _queue_lock

    path = tmp_path / "review-queue.json"
    lock = path.with_name(".review-queue.json.lock")
    with _queue_lock(path):
        assert lock.is_file()
    assert not lock.exists()


def test_queue_lock_reclaims_stale_lock(tmp_path: Path) -> None:
    from media_quotes.cli.maintenance import _queue_lock

    path = tmp_path / "review-queue.json"
    lock = path.with_name(".review-queue.json.lock")
    lock.write_text("old\n", encoding="utf-8")
    stale_time = time.time() - 120.0
    os.utime(lock, (stale_time, stale_time))
    with _queue_lock(path):
        assert lock.is_file()
    assert not lock.exists()


def test_exit_code_contract() -> None:
    assert exit_code_for() == 0
    assert exit_code_for(ok=False) == 1
    assert exit_code_for(gaps=["x"]) == 2
    assert exit_code_for(config_error=True) == 3


def test_review_queue_list_and_decide(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    evidence = ObsolescenceEvidence(
        old_quote_id="Q-20261118-0001",
        old_content_hash="a" * 64,
        newer_article_id="art-2",
        newer_content_hash="b" * 64,
        newer_is_first_hand=True,
        newer_rights_cleared=True,
        superseding_span="new facts",
        decision_id="dec-1",
        rationale="updated by primary source",
    )
    flag = ObsolescenceFlag(evidence=evidence, confidence=0.95, state="pending")
    queue_file = settings.audit_dir / "review-queue.json"
    queue_file.write_text(json.dumps({"flags": [flag.model_dump(mode="json")]}), encoding="utf-8")

    assert _do_review(settings, action="list") == 0
    assert _do_review(settings, action="decide", quote_id="Q-20261118-0001") == 0
    payload = json.loads(queue_file.read_text(encoding="utf-8"))
    assert payload["flags"][0]["state"] == "confirmed"


def test_review_queue_missing_or_unknown_quote(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    with pytest.raises(ConfigError, match="--quote-id is required"):
        _do_review(settings, action="decide", quote_id=None)
    assert _do_review(settings, action="decide", quote_id="Q-NONEXISTENT") == 1


def test_review_unknown_action(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        _do_review(settings_for(tmp_path), action="unknown")


def test_retention_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _do_retention(settings_for(tmp_path), dry_run=True) == 0
    out = capsys.readouterr().out
    assert "partitions_to_detach" in out


def test_retention_live_attempt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _do_retention(settings_for(tmp_path), dry_run=False) == 0
    out = capsys.readouterr().out
    assert "Live retention job is not available" in out


def test_retention_dry_run_never_writes_partitions(tmp_path: Path, monkeypatch) -> None:
    def forbid_db(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("dry-run must not write storage_partitions")

    monkeypatch.setattr("media_quotes.db.repository.sync_storage_partitions", forbid_db)
    settings = settings_for(tmp_path).model_copy(update={"persist_to_postgres": True})
    assert _do_retention(settings, dry_run=True) == 0


def test_restore_drill_needs_two_exports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _do_restore_drill(settings_for(tmp_path)) == 1
    assert "--reference" in capsys.readouterr().out


def test_restore_drill_with_two_files_and_reference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = settings_for(tmp_path)
    settings.export_dir.mkdir(parents=True, exist_ok=True)
    file1 = settings.export_dir / "quotes-2026-11-19.jsonl"
    file2 = settings.export_dir / "quotes-2026-11-18.jsonl"
    row = {
        "quote_id": "Q-20261118-0001",
        "quote_text_original": "Valid quote text here.",
        "quote_html": "",
        "quote_text_en": "",
        "source_id": "reuters",
        "source_name": "Reuters",
        "publication_name": "Reuters fixture",
        "source_tier": 1,
        "source_country_region": "Global",
        "source_type": "independent",
        "rights_status": "blocked",
        "access_mode": "scraped_prohibited",
        "license_reference": "",
        "discovery_method": "sitemap",
        "claim_only": False,
        "original_reporting_flag": "first-hand",
        "source_language": "en",
        "publication_date": "2026-11-18",
        "article_name": "Test article",
        "article_author": "Reporter",
        "article_url": "https://example.com/test",
        "extraction_date": "2026-11-18T12:00:00Z",
        "section_id": "war_intensity",
        "is_obsolete": False,
        "obsolete_reason": "",
        "content_hash": "a" * 64,
        "verification": {
            "verbatim_match": True,
            "metadata_verified": True,
            "method": "nfc_exact_span_v1",
            "verified_at": "2026-11-18T12:00:00Z",
        },
        "notes": "",
    }
    data = json.dumps(row) + "\n"
    file1.write_text(data, encoding="utf-8")
    file2.write_text(data, encoding="utf-8")

    assert _do_restore_drill(settings) == 0
    assert "PASS" in capsys.readouterr().out

    assert _do_restore_drill(settings, reference=file2) == 0
    assert "PASS" in capsys.readouterr().out


def test_restore_drill_uses_topic_export_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = settings_for(tmp_path)
    topic_dir = settings.export_dir / "ai-policy"
    topic_dir.mkdir(parents=True, exist_ok=True)
    current = topic_dir / "quotes-2026-11-19.jsonl"
    reference = topic_dir / "quotes-2026-11-18.jsonl"
    current.write_text("", encoding="utf-8")
    reference.write_text("", encoding="utf-8")

    assert _do_restore_drill(settings, reference=reference, topic_id="ai-policy") == 0
    assert str(current) in capsys.readouterr().out
