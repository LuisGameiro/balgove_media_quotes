"""Regression tests for the second repository audit."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from media_quotes.adapters.html_extractor import parse_newspaper_html
from media_quotes.cli import app
from media_quotes.cli.common import _load_registry
from media_quotes.cli.run import _process_source
from media_quotes.config import Settings

ROOT = Path(__file__).resolve().parents[2]
REPORT_DATE = date(2026, 11, 19)

_SHORT_HTML = """<html><body><article><h1>Short</h1><p>Officials said
<span class="quote">"We are committed to talks and will continue the negotiations in
good faith next week"</span> the minister added.</p></article></body></html>"""


def test_low_quality_article_yields_no_quotes() -> None:
    parsed = parse_newspaper_html(_SHORT_HTML, source_url="https://example.invalid/a")
    assert parsed["quality_ok"] is False
    assert parsed["quote_spans"] == []


def test_licensed_source_fixture_quotes_are_published() -> None:
    settings = Settings(_env_file=None)
    row = next(row for row in _load_registry(settings) if row.source_id == "guardian")
    assert row.usable
    records, _coverage, _gaps, _events, _rejected, _offsets = _process_source(
        row,
        settings=settings,
        run_id="regression",
        window_start=date(2026, 11, 6),
        report_date=REPORT_DATE,
        used_quote_ids=set(),
    )
    assert records
    assert all(record.original_reporting_flag == "first-hand" for record in records)


@pytest.mark.parametrize("flag", ["unknown", "relayed wire copy"])
def test_non_first_hand_source_config_withholds_quotes(flag: str, monkeypatch: Any) -> None:
    from media_quotes.cli import run as cli_run
    from media_quotes.source_configs import load_source_config

    settings = Settings(_env_file=None)
    row = next(row for row in _load_registry(settings) if row.source_id == "guardian")
    assert row.usable
    config = load_source_config("guardian").model_copy(update={"original_reporting_flag": flag})
    seen: list[str] = []
    real_verify = cli_run.verify_pipeline

    def spy(candidates: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["policy_flags"]["original_reporting_flag"])
        return real_verify(candidates, **kwargs)

    monkeypatch.setattr(cli_run, "verify_pipeline", spy)
    records, _coverage, _gaps, _events, rejected, _offsets = _process_source(
        row,
        settings=settings,
        run_id="regression-relayed",
        window_start=date(2026, 11, 6),
        report_date=REPORT_DATE,
        used_quote_ids=set(),
        source_config=config,
    )
    assert flag in seen
    assert records == []
    assert rejected > 0


def test_fetch_without_live_only_plans(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    settings = Settings(
        _env_file=None,
        sources_csv=ROOT / "data/sources.csv",
        sources_dir=ROOT / "data/sources",
        audit_dir=tmp_path / "audit",
        export_dir=tmp_path / "export",
        persist_to_postgres=False,
    )
    monkeypatch.setattr(app, "get_settings", lambda: settings)
    assert app.main(["fetch", "--source-id", "guardian"]) == 0
    report = capsys.readouterr().out
    assert '"dry_run": true' in report
    assert '"network_calls": 0' in report
