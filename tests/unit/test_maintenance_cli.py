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
    audit = tmp_path / "audit"
    audit.mkdir(exist_ok=True)
    export = tmp_path / "export"
    export.mkdir(exist_ok=True)
    return Settings(_env_file=None, audit_dir=audit, export_dir=export)


def flag_payload() -> dict[str, object]:
    evidence = ObsolescenceEvidence(
        old_quote_id="Q-20261101-0001",
        old_content_hash="a" * 64,
        newer_article_id="art",
        newer_content_hash="b" * 64,
        newer_is_first_hand=True,
        newer_rights_cleared=True,
        superseding_span="new fact",
        decision_id="d1",
        rationale="superseded",
    )
    return ObsolescenceFlag(evidence=evidence, confidence=0.9).model_dump(mode="json")


def test_review_list_and_decide(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings = settings_for(tmp_path)
    (settings.audit_dir / "review-queue.json").write_text(
        json.dumps({"flags": [flag_payload()]}), encoding="utf-8"
    )
    assert _do_review(settings, action="list") == 0
    assert "pending" in capsys.readouterr().out

    assert _do_review(
        settings, action="decide", quote_id="Q-20261101-0001", reason_code="human-review"
    ) == 0
    assert "confirmed" in capsys.readouterr().out
    stored = json.loads((settings.audit_dir / "review-queue.json").read_text(encoding="utf-8"))
    assert stored["flags"][0]["state"] == "confirmed"

    assert _do_review(
        settings, action="decide", quote_id="missing", reason_code="human-review"
    ) == 1
    with pytest.raises(ConfigError):
        _do_review(settings, action="nope")


def test_retention_prints_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert _do_retention(settings_for(tmp_path), dry_run=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "partitions_to_detach" in payload


def test_restore_drill_needs_two_exports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _do_restore_drill(settings_for(tmp_path)) == 1
    assert "--reference" in capsys.readouterr().out
