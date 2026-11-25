"""Maintenance CLI — review queue, retention plan and restore drill (offline)."""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

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


def test_queue_lock_lives_in_common_and_is_re_exported(tmp_path: Path) -> None:
    from media_quotes.cli import common
    from media_quotes.cli.maintenance import _queue_lock as maintenance_lock

    assert maintenance_lock is common._queue_lock


def test_concurrent_review_decisions_are_not_lost(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    queue_file = settings.audit_dir / "review-queue.json"
    queue_file.write_text(
        json.dumps({"flags": [_flag_payload("Q-A"), _flag_payload("Q-B")]}), encoding="utf-8"
    )
    errors: list[Exception] = []

    def decide(quote_id: str) -> None:
        try:
            _do_review(settings, action="decide", quote_id=quote_id)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=decide, args=(quote_id,)) for quote_id in ("Q-A", "Q-B")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert not errors
    payload = json.loads(queue_file.read_text(encoding="utf-8"))
    assert {flag["evidence"]["old_quote_id"]: flag["state"] for flag in payload["flags"]} == {
        "Q-A": "confirmed",
        "Q-B": "confirmed",
    }
    assert not queue_file.with_name(".review-queue.json.lock").exists()


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


def _flag_payload(quote_id: str, state: str = "pending") -> dict[str, object]:
    return {
        "evidence": {
            "old_quote_id": quote_id,
            "old_content_hash": "a" * 64,
            "newer_article_id": "art-2",
            "newer_content_hash": "b" * 64,
            "newer_is_first_hand": True,
            "newer_rights_cleared": True,
            "superseding_span": "Updated ceasefire statement.",
            "decision_id": "dec-1",
            "rationale": "Superseded by newer official statement.",
        },
        "confidence": 0.92,
        "state": state,
        "raised_at": "2026-11-19T09:00:00Z",
    }


def test_fixture_and_live_review_queues_coexist(tmp_path: Path) -> None:
    from media_quotes.cli.common import load_review_flags

    settings = settings_for(tmp_path)
    settings.audit_dir.mkdir(parents=True, exist_ok=True)
    fixture_path = settings.audit_dir / "review-queue.json"
    live_path = settings.audit_dir / "review-queue-live_feed.json"
    bing_path = settings.audit_dir / "review-queue-live_bing.json"
    shared_fixture = {"flags": [_flag_payload("Q-SHARED")]}
    shared_live = {"flags": [_flag_payload("Q-SHARED")]}
    fixture_path.write_text(json.dumps(shared_fixture), encoding="utf-8")
    live_path.write_text(json.dumps(shared_live), encoding="utf-8")

    # The same quote ID in two provenance queues must not be merged into one loader.
    assert [f.evidence.old_quote_id for f in load_review_flags(settings)] == ["Q-SHARED"]
    assert [f.evidence.old_quote_id for f in load_review_flags(settings, "live_feed")] == [
        "Q-SHARED"
    ]

    assert _do_review(settings, action="decide", quote_id="Q-SHARED", provenance="live_feed") == 0
    assert json.loads(live_path.read_text(encoding="utf-8"))["flags"][0]["state"] == "confirmed"
    assert json.loads(fixture_path.read_text(encoding="utf-8")) == shared_fixture

    assert _do_review(settings, action="seed", quote_id="Q-BING-SEED", provenance="live_bing") == 0
    assert bing_path.is_file()
    seeded = json.loads(bing_path.read_text(encoding="utf-8"))["flags"]
    assert seeded[0]["evidence"]["old_quote_id"] == "Q-BING-SEED"
    assert json.loads(fixture_path.read_text(encoding="utf-8")) == shared_fixture
    assert json.loads(live_path.read_text(encoding="utf-8"))["flags"][0]["state"] == "confirmed"


def test_review_cli_provenance_option_reaches_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import media_quotes.cli as cli
    from media_quotes.cli.app import main

    settings = settings_for(tmp_path)
    calls: list[dict[str, object]] = []

    def spy(_settings: Settings, **kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(cli, "_do_review", spy)
    monkeypatch.setattr("media_quotes.cli.app.get_settings", lambda: settings)

    assert main(["review", "--action", "list", "--provenance", "live_feed"]) == 0
    assert main(["review", "--action", "list"]) == 0
    assert calls[0]["provenance"] == "live_feed"
    assert calls[1]["provenance"] == "fixture"

    with pytest.raises(SystemExit):
        main(["review", "--provenance", "not-a-provenance"])


def _retention_report(out: str) -> dict[str, Any]:
    lines = out.splitlines()
    start = next(i for i, line in enumerate(lines) if line == "{")
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "}")
    return json.loads("\n".join(lines[start : end + 1]))


def test_retention_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _do_retention(settings_for(tmp_path), dry_run=True) == 0
    out = capsys.readouterr().out
    report = _retention_report(out)
    assert report["partitions_to_detach"] == []
    assert report["partitions_hot"] == []
    assert "no detach plan is fabricated" in out


def test_retention_live_attempt(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _do_retention(settings_for(tmp_path), dry_run=False) == 0
    out = capsys.readouterr().out
    report = _retention_report(out)
    assert report["partitions_to_detach"] == []
    assert "no detach plan is fabricated" in out
    assert "Live retention job is not available" in out


def test_retention_dry_run_never_opens_db(tmp_path: Path, monkeypatch) -> None:
    def forbid_db(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("dry-run must not open a DB session")

    monkeypatch.setattr("media_quotes.pipeline._default_session_context", forbid_db)
    monkeypatch.setattr("media_quotes.db.repository.observed_audit_months", forbid_db)
    settings = settings_for(tmp_path).model_copy(update={"persist_to_postgres": True})
    assert _do_retention(settings, dry_run=True) == 0


def test_retention_live_uses_observed_months(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    class _MonthRows:
        def __init__(self, months: list[datetime]) -> None:
            self._months = months

        def scalars(self) -> _MonthRows:
            return self

        def all(self) -> list[datetime]:
            return self._months

    class _ObservedSession:
        def execute(self, statement: object) -> _MonthRows:
            return _MonthRows([datetime(2026, 10, 6, 9, 0), datetime(2026, 11, 3, 9, 0)])

    @contextmanager
    def fake_session_context(_settings: Settings):  # type: ignore[no-untyped-def]
        yield _ObservedSession()

    monkeypatch.setattr("media_quotes.pipeline._default_session_context", fake_session_context)
    settings = settings_for(tmp_path).model_copy(
        update={"persist_to_postgres": True, "report_date": date(2026, 11, 19)}
    )
    assert _do_retention(settings, dry_run=False) == 0
    out = capsys.readouterr().out
    report = _retention_report(out)
    assert report["partitions_to_detach"] == ["audit_events_2026_10"]
    assert report["partitions_hot"] == ["audit_events_2026_11"]
    assert report["partition_months_observed"] is True


def test_restore_drill_needs_two_exports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _do_restore_drill(settings_for(tmp_path)) == 1
    assert "--reference" in capsys.readouterr().out


def test_restore_drill_with_two_files_and_reference(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = settings_for(tmp_path)
    fixtures_dir = settings.export_dir / "fixtures"
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    file1 = fixtures_dir / "quotes-2026-11-19.jsonl"
    file2 = fixtures_dir / "quotes-2026-11-18.jsonl"
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
    topic_dir = settings.export_dir / "fixtures" / "ai-policy"
    topic_dir.mkdir(parents=True, exist_ok=True)
    current = topic_dir / "quotes-2026-11-19.jsonl"
    reference = topic_dir / "quotes-2026-11-18.jsonl"
    current.write_text("", encoding="utf-8")
    reference.write_text("", encoding="utf-8")

    assert _do_restore_drill(settings, reference=reference, topic_id="ai-policy") == 0
    assert str(current) in capsys.readouterr().out
