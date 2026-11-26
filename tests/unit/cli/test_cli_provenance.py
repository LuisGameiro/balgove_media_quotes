"""CLI digest notices and reconstructed audit run IDs honour acquisition provenance."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from conftest import settings_for
from media_quotes.cli.audit import _do_audit
from media_quotes.cli.common import _run_id
from media_quotes.cli.ops import _do_digest
from media_quotes.config import Settings


def _live_export_from_fixture(settings: Settings, day: date) -> Path:
    from media_quotes.pipeline import run_daily_pipeline

    run_daily_pipeline(settings, report_date=day, topic_id="ukraine-war")
    fixture = settings.export_dir / "fixtures" / f"quotes-{day.isoformat()}.jsonl"
    live = settings.export_dir / f"quotes-{day.isoformat()}.jsonl"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_bytes(fixture.read_bytes())
    return live


def _settings(tmp_path: Path, repo_root: Path) -> Settings:
    return settings_for(
        tmp_path,
        report_date=date(2026, 11, 19),
        sections_csv=repo_root / "data" / "sections.csv",
        schema_path=repo_root / "data" / "quotes.schema.json",
        fixtures_dir=repo_root / "data" / "fixtures",
    )


def test_digest_notice_follows_provenance(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    settings = _settings(tmp_path, repo_root)
    day = date(2026, 11, 19)
    _live_export_from_fixture(settings, day)

    live_out = tmp_path / "live.md"
    assert _do_digest(settings, report_date=day, out=str(live_out), provenance="live_feed") == 0
    live_text = live_out.read_text(encoding="utf-8")
    assert "LIVE COLLECTION" in live_text
    assert "OFFLINE FIXTURE REPLAY" not in live_text

    fixture_out = tmp_path / "fixture.md"
    assert _do_digest(settings, report_date=day, out=str(fixture_out)) == 0
    fixture_text = fixture_out.read_text(encoding="utf-8")
    assert "OFFLINE FIXTURE REPLAY" in fixture_text
    assert "LIVE COLLECTION" not in fixture_text


def test_reconstructed_live_audit_uses_live_run_id(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[3]
    settings = _settings(tmp_path, repo_root)
    day = date(2026, 11, 19)
    _live_export_from_fixture(settings, day)

    assert _do_audit(settings, report_date=day, run_id=None, out=None, provenance="live_feed") in (
        0,
        2,
    )
    stored = settings.audit_dir / f"audit-{day.isoformat()}.json"
    assert stored.is_file()
    import json

    payload = json.loads(stored.read_text(encoding="utf-8"))
    expected_live = _run_id(day, settings, topic_id="ukraine-war", provenance="live_feed")
    expected_fixture = _run_id(day, settings, topic_id="ukraine-war", provenance="fixture")
    assert payload["run_id"] == expected_live
    assert payload["run_id"] != expected_fixture


def test_live_audit_uses_stored_report_and_labels_reconstruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_quotes.cli.app import main

    repo_root = Path(__file__).resolve().parents[3]
    settings = _settings(tmp_path, repo_root)
    monkeypatch.setattr("media_quotes.cli.app.get_settings", lambda: settings)
    day = date(2026, 11, 19)
    _live_export_from_fixture(settings, day)

    # No stored live report: the audit is rebuilt and must say so.
    assert _do_audit(settings, report_date=day, run_id=None, out=None, provenance="live_feed") in (
        0,
        2,
    )
    stored = settings.audit_dir / f"audit-{day.isoformat()}.json"
    md_path = stored.with_suffix(".md")
    payload = json.loads(stored.read_text(encoding="utf-8"))
    assert payload["reconstructed"] is True
    assert "Reconstructed: true" in md_path.read_text(encoding="utf-8")

    # A stored live report wins unchanged: no reconstruction flag, no label.
    marker = {
        "topic_id": "ukraine-war",
        "provenance": "live_feed",
        "report_date": day.isoformat(),
        "mode": "stored live report",
        "run_id": "stored-live-run",
        "verified_quotes": 999,
        "rejected_candidates": 0,
        "coverage_gaps": [],
        "artifacts": {},
        "checks": {"gates_pass": True},
    }
    stored.write_text(json.dumps(marker), encoding="utf-8")
    assert main(["audit", "--date", day.isoformat(), "--provenance", "live_feed"]) == 0
    preserved = json.loads(stored.read_text(encoding="utf-8"))
    assert preserved["run_id"] == "stored-live-run"
    assert preserved["verified_quotes"] == 999
    assert "reconstructed" not in preserved
    assert "Reconstructed" not in md_path.read_text(encoding="utf-8")


def test_digest_loads_review_flags_with_selected_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import media_quotes.cli.common as common

    repo_root = Path(__file__).resolve().parents[3]
    settings = _settings(tmp_path, repo_root)
    day = date(2026, 11, 19)
    _live_export_from_fixture(settings, day)

    seen: list[str] = []
    real_load = common.load_review_flags

    def spy(settings_arg: Any, provenance: Any = "fixture") -> Any:
        seen.append(str(provenance))
        return real_load(settings_arg, provenance)  # type: ignore[arg-type]

    monkeypatch.setattr(common, "load_review_flags", spy)
    out = tmp_path / "live-flags.md"
    assert _do_digest(settings, report_date=day, out=str(out), provenance="live_feed") == 0
    assert seen == ["live_feed"]


def test_provenance_option_reaches_audit_digest_export_and_drill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_quotes.cli.app import main

    repo_root = Path(__file__).resolve().parents[3]
    settings = _settings(tmp_path, repo_root)
    monkeypatch.setattr("media_quotes.cli.app.get_settings", lambda: settings)

    calls: dict[str, dict[str, Any]] = {}

    def make_spy(name: str) -> Any:
        def spy(_settings: Settings, **kwargs: Any) -> int:
            calls[name] = kwargs
            return 0

        return spy

    handler_modules = {
        "_do_audit": "audit",
        "_do_digest": "ops",
        "_do_export": "ops",
        "_do_restore_drill": "maintenance",
    }
    for name, module in handler_modules.items():
        monkeypatch.setattr(f"media_quotes.cli.{module}.{name}", make_spy(name))

    assert main(["audit", "--provenance", "live_feed"]) == 0
    assert calls["_do_audit"]["provenance"] == "live_feed"

    assert main(["digest"]) == 0
    assert calls["_do_digest"]["provenance"] == "fixture"

    assert main(["export", "--provenance", "live_feed", "--from-db"]) == 0
    assert calls["_do_export"]["provenance"] == "live_feed"
    assert calls["_do_export"]["from_db"] is True

    assert main(["restore-drill", "--provenance", "live_bing"]) == 0
    assert calls["_do_restore_drill"]["provenance"] == "live_bing"

    with pytest.raises(SystemExit):
        main(["digest", "--provenance", "not-a-provenance"])
