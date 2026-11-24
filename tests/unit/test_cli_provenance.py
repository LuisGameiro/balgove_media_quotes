"""CLI digest notices and reconstructed audit run IDs honour acquisition provenance."""

from __future__ import annotations

from datetime import date
from pathlib import Path

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
    return Settings(
        _env_file=None,
        persist_to_postgres=False,
        report_date=date(2026, 11, 19),
        digest_dir=tmp_path / "digests",
        export_dir=tmp_path / "export",
        logs_dir=tmp_path / "logs",
        audit_dir=tmp_path / "audit",
        sections_csv=repo_root / "data" / "sections.csv",
        schema_path=repo_root / "data" / "quotes.schema.json",
        fixtures_dir=repo_root / "data" / "fixtures",
    )


def test_digest_notice_follows_provenance(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
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
    repo_root = Path(__file__).resolve().parents[2]
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
