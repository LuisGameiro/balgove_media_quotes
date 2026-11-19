"""Maintenance CLI — review queue, retention plan and restore drill (offline)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


def test_review_unknown_action(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        _do_review(settings_for(tmp_path), action="unknown")


def test_retention_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _do_retention(settings_for(tmp_path), dry_run=True) == 0
    out = capsys.readouterr().out
    assert "partitions_to_detach" in out


def test_restore_drill_needs_two_exports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _do_restore_drill(settings_for(tmp_path)) == 1
    assert "--reference" in capsys.readouterr().out
