"""Contracts for live-by-default runs and explicit fixture/cached-blob modes."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from media_quotes.cli import app
from media_quotes.config import Settings
from media_quotes.exceptions import ConfigError
from media_quotes.pipeline import _fetch_live_blobs, run_daily_pipeline


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        output_dir=tmp_path / "output",
        persist_to_postgres=False,
        llm_provider="local",
    )


def test_live_pipeline_prefetches_then_collects_blobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    fetch_calls: list[dict[str, Any]] = []
    collect_calls: list[dict[str, Any]] = []

    def fake_fetch(*args: Any, **kwargs: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        fetch_calls.append(kwargs)
        return [], []

    def fake_collect(*args: Any, **kwargs: Any) -> tuple[Any, ...]:
        collect_calls.append(kwargs)
        return [], [], [], [], 0, {}

    monkeypatch.setattr("media_quotes.pipeline._fetch_live_blobs", fake_fetch)
    monkeypatch.setattr("media_quotes.cli.run._collect", fake_collect)

    result = run_daily_pipeline(settings, report_date=date(2026, 11, 24), run_id="run-live")

    assert fetch_calls
    assert collect_calls[0]["source_mode"] == "blobs"
    assert result.audit_json_path == settings.audit_dir / "audit-2026-11-24.json"
    audit = json.loads(result.audit_json_path.read_text(encoding="utf-8"))
    assert audit["version_manifest"]["fixture_replay"] is False


def test_live_dry_run_skips_network_and_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("dry run must not fetch or collect")

    monkeypatch.setattr("media_quotes.pipeline._fetch_live_blobs", unexpected)
    monkeypatch.setattr("media_quotes.cli.run._collect", unexpected)

    result = run_daily_pipeline(
        settings, report_date=date(2026, 11, 24), dry_run=True, run_id="run-dry"
    )

    assert result.dry_run
    assert result.coverage
    assert not result.records
    assert not settings.export_dir.exists()


def test_cli_run_defaults_to_live_and_supports_explicit_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(app, "get_settings", lambda: settings)
    monkeypatch.setattr(
        "media_quotes.cli._do_run",
        lambda _settings, **kwargs: calls.append(kwargs) or 0,
    )

    assert app.main(["run", "--date", "2026-11-24"]) == 0
    assert app.main(["run", "--date", "2026-11-24", "--fixtures"]) == 0
    assert app.main(["run", "--date", "2026-11-24", "--from-blobs"]) == 0

    assert [call["source_mode"] for call in calls] == ["live", "fixtures", "blobs"]


def test_cli_audit_digest_export_support_explicit_fixtures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    calls: list[tuple[str, Path, dict[str, Any]]] = []
    monkeypatch.setattr(app, "get_settings", lambda: settings)
    for name in ("_do_audit", "_do_digest", "_do_export"):
        monkeypatch.setattr(
            f"media_quotes.cli.{name}",
            lambda s, _name=name, **kwargs: calls.append((_name, s.export_dir, kwargs)) or 0,
        )

    for command in ("audit", "digest", "export"):
        assert app.main([command, "--date", "2026-11-24", "--fixtures"]) == 0
    assert app.main(["audit", "--date", "2026-11-24"]) == 0

    fixture_root = settings.for_source_mode("fixtures").export_dir
    assert [call[1] for call in calls[:3]] == [fixture_root] * 3
    assert calls[3][1] == settings.export_dir
    assert calls[0][2]["source_mode"] == "fixtures"
    assert calls[3][2]["source_mode"] == "live"


def test_cli_run_rejects_conflicting_modes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "get_settings", lambda: _settings(tmp_path))

    with pytest.raises(SystemExit):
        app.main(["run", "--date", "2026-11-24", "--fixtures", "--from-blobs"])


def test_pipeline_rejects_unknown_source_mode(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="source_mode"):
        run_daily_pipeline(
            _settings(tmp_path),
            report_date=date(2026, 11, 24),
            source_mode="fixture",
        )


def test_live_prefetch_skips_blocked_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    rows = [
        SimpleNamespace(
            source_id="bbc",
            tier=1,
            usable=True,
            tier_block=False,
            feed_url="https://feeds.bbci.co.uk/news/rss.xml",
            license_reference="LIC-BBC",
        ),
        SimpleNamespace(
            source_id="reuters",
            tier=1,
            usable=False,
            tier_block=True,
            feed_url="https://www.reuters.com/arc/outboundfeeds/sitemap/",
            license_reference="LIC-REUTERS",
        ),
    ]
    collected: list[str] = []

    def fake_collect(row: Any, *args: Any, **kwargs: Any) -> Any:
        collected.append(row.source_id)
        return SimpleNamespace(
            discovered=[{"url": "https://www.bbc.co.uk/article"}],
            articles=[SimpleNamespace(error="")],
        )

    monkeypatch.setattr("media_quotes.cli.common._load_registry", lambda _settings: rows)
    monkeypatch.setattr("media_quotes.cli.common._load_source_configs", lambda _settings: {})
    monkeypatch.setattr("media_quotes.collection.collect_feed", fake_collect)

    gaps, events = _fetch_live_blobs(
        settings,
        run_id="run-live",
        report_date=date(2026, 11, 24),
        topic_pack=None,
        topic_id="ukraine-war",
        tier=None,
    )

    assert collected == ["bbc"]
    assert not gaps
    assert events[0]["source_id"] == "bbc"
    assert events[0]["license_reference"] == "LIC-BBC"


def test_fixture_runs_never_persist_even_when_explicitly_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path).model_copy(update={"persist_to_postgres": True})

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("fixture runs must not open a database session")

    monkeypatch.setattr("media_quotes.cli.run._collect", lambda *a, **k: ([], [], [], [], 0, {}))

    result = run_daily_pipeline(
        settings,
        report_date=date(2026, 11, 24),
        run_id="run-fixture",
        source_mode="fixtures",
        persist_to_postgres=True,
        session_context=forbidden,
    )

    assert result.persistence == {}
    audit = json.loads(result.audit_json_path.read_text(encoding="utf-8"))
    assert audit["checks"]["no_database_writes"] is True
    assert audit["mode"] == "synthetic fixture replay (test output)"


@pytest.mark.parametrize(
    ("setting", "effective", "mode", "no_writes"),
    [
        (True, False, "live/blob file output", True),
        (False, True, "Postgres persistent", False),
    ],
)
def test_audit_report_uses_effective_persistence(
    tmp_path: Path, setting: bool, effective: bool, mode: str, no_writes: bool
) -> None:
    from media_quotes.cli.audit import _audit_report

    settings = _settings(tmp_path).model_copy(update={"persist_to_postgres": setting})
    report = _audit_report(
        settings,
        report_date=date(2026, 11, 24),
        run_id="r",
        records=[],
        coverage=[],
        gaps=[],
        events=[],
        rejected=0,
        should_persist=effective,
    )
    assert report["mode"] == mode
    assert report["checks"]["no_database_writes"] is no_writes


def test_live_prefetch_and_collection_gaps_are_deduped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path)
    live_gap = {
        "source_id": "reuters",
        "status": "blocked",
        "gap_reason": "live_fetch_failed",
        "detail": "timeout",
    }
    event = {"trace_id": "t", "license_reference": "L", "step": "fetch"}
    monkeypatch.setattr(
        "media_quotes.pipeline._fetch_live_blobs", lambda *a, **k: ([live_gap], [event])
    )
    monkeypatch.setattr(
        "media_quotes.cli.run._collect",
        lambda *a, **k: ([], [], [{**live_gap, "detail": "duplicate"}], [], 0, {}),
    )

    result = run_daily_pipeline(settings, report_date=date(2026, 11, 24), run_id="run-dedup")

    assert list(result.gaps) == [live_gap]
    assert result.events == (event,)
    assert result.status == "partial"


def test_pipeline_status_follows_audit_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_quotes.cli import audit as audit_mod

    settings = _settings(tmp_path)
    monkeypatch.setattr("media_quotes.cli.run._collect", lambda *a, **k: ([], [], [], [], 0, {}))

    failed = run_daily_pipeline(
        settings, report_date=date(2026, 11, 24), run_id="run-failed", source_mode="fixtures"
    )
    assert failed.status == "failed"

    original_report = audit_mod._audit_report

    def forced_gates(*args: Any, **kwargs: Any) -> dict[str, Any]:
        report = original_report(*args, **kwargs)
        report["checks"]["gates_pass"] = True
        return report

    monkeypatch.setattr(audit_mod, "_audit_report", forced_gates)
    succeeded = run_daily_pipeline(
        settings, report_date=date(2026, 11, 24), run_id="run-ok", source_mode="fixtures"
    )
    assert succeeded.status == "succeeded"

    monkeypatch.setattr(
        "media_quotes.cli.run._collect",
        lambda *a, **k: (
            [],
            [],
            [{"source_id": "x", "status": "degraded", "gap_reason": "no_coverage"}],
            [],
            0,
            {},
        ),
    )
    partial = run_daily_pipeline(
        settings, report_date=date(2026, 11, 24), run_id="run-partial", source_mode="fixtures"
    )
    assert partial.status == "partial"


def test_persistence_failure_raises_domain_error(tmp_path: Path) -> None:
    from media_quotes.exceptions import MediaQuotesError
    from media_quotes.pipeline import DailyPipelineResult, _persist_result

    settings = _settings(tmp_path)
    result = DailyPipelineResult(
        run_id="r",
        report_date=date(2026, 11, 24),
        dry_run=False,
        records=(),
        coverage=(),
        gaps=(),
        events=(),
        rejected=0,
        rejected_total=0,
        status="succeeded",
    )

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise OSError("db down")

    with pytest.raises(MediaQuotesError, match="db down"):
        _persist_result(result, settings, pipeline="mq-run", session_context=broken)


def test_source_with_no_references_is_marked_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from media_quotes.cli import run as run_mod
    from media_quotes.cli.common import _load_registry

    settings = _settings(tmp_path)
    row = next(row for row in _load_registry(settings) if row.source_id == "guardian")
    assert row.usable

    class _EmptyAdapter:
        mode = "blobs"

        def discover(self, start: Any, end: Any) -> list[str]:
            return []

        def policy_flags(self) -> dict[str, Any]:
            return {}

    monkeypatch.setattr(run_mod, "build_adapter", lambda *a, **k: _EmptyAdapter())
    _records, coverage, gaps, _events, _rejected, _offsets = run_mod._process_source(
        row,
        settings=settings,
        run_id="r",
        window_start=date(2026, 11, 18),
        report_date=date(2026, 11, 24),
        used_quote_ids=set(),
        source_mode="blobs",
    )
    assert coverage["status"] == "degraded"
    assert coverage["gap_reason"] == "no_coverage"
    assert gaps


@pytest.mark.parametrize(
    ("source_mode", "expected", "forbidden"),
    [
        ("fixtures", ("verified fixture quote(s)", "fixture replay logged"), ()),
        (
            "blobs",
            ("verified quote(s)", "cached blob ingest"),
            ("fixture quote(s)", "fixture replay logged"),
        ),
        (
            "live",
            ("verified quote(s)", "live collection attempted"),
            ("fixture quote(s)", "fixture replay logged"),
        ),
    ],
)
def test_run_output_wording_matches_source_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: Any,
    source_mode: str,
    expected: tuple[str, ...],
    forbidden: tuple[str, ...],
) -> None:
    from media_quotes.cli.run import _do_run
    from media_quotes.pipeline import DailyPipelineResult

    result = DailyPipelineResult(
        run_id="r",
        report_date=date(2026, 11, 24),
        dry_run=False,
        records=(),
        coverage=(),
        gaps=(
            {
                "source_id": "reuters",
                "gap_reason": "live_fetch_failed",
                "detail": "timeout",
                "mandatory": True,
                "rights_status": "blocked",
            },
        ),
        events=(),
        rejected=0,
        rejected_total=0,
        status="partial",
    )
    monkeypatch.setattr("media_quotes.pipeline.run_daily_pipeline", lambda *a, **k: result)

    _do_run(
        _settings(tmp_path),
        report_date=date(2026, 11, 24),
        dry_run=False,
        run_id="r",
        verbose=False,
        source_mode=source_mode,
    )
    out = capsys.readouterr().out
    for phrase in expected:
        assert phrase in out
    for phrase in forbidden:
        assert phrase not in out


def test_db_reconstruction_defaults_to_unverified() -> None:
    from media_quotes.db.repository import read_export_records

    class _Mappings:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows

        def mappings(self) -> list[dict[str, Any]]:
            return self._rows

    class _Session:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self._rows = rows

        def execute(self, statement: Any) -> _Mappings:
            return _Mappings(self._rows)

    row: dict[str, Any] = {
        "q_id": "Q-20261119-0001",
        "q_text": "Official said talks continued.",
        "q_text_en": "",
        "q_speaker": "",
        "q_char_start": 0,
        "q_meta": {},
        "q_details": {},
        "q_method": "",
        "q_topic": "ukraine-war",
        "q_topic_status": "accepted",
        "q_rule_version": "",
        "q_score": 0.0,
        "q_terms": [],
        "q_language": "en",
        "q_lang_conf": 0.0,
        "q_t_model": "",
        "q_t_prompt": "",
        "q_t_hash": "",
        "q_t_status": "",
        "a_url": "https://news.example/story",
        "a_title": "Story",
        "a_language": "en",
        "a_date": date(2026, 11, 19),
        "a_meta": {},
        "a_hash": "a" * 64,
        "s_id": "guardian",
        "s_name": "Guardian",
        "s_tier": 2,
        "s_region": "United Kingdom",
        "s_type": "independent",
        "s_rights": "license_held",
        "s_access": "public_rss_permitted",
        "s_license": "LIC-GUARDIAN",
        "s_discovery": "rss",
        "s_claim": False,
        "s_language": "en",
        "o_obsolete": False,
        "o_reason": "",
        "o_created_at": None,
    }
    records = read_export_records(_Session([row]), report_date=date(2026, 11, 19), window_days=7)
    assert len(records) == 1
    assert records[0].verification.verbatim_match is False
    assert records[0].verification.metadata_verified is False
