"""Regression tests for the third repository audit."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from media_quotes.cli.app import _build_parser
from media_quotes.cli.audit import _audit_report, _write_audit
from media_quotes.config import Settings
from media_quotes.orchestration import _load_audit_report

ROOT = Path(__file__).resolve().parents[2]
REPORT_DATE = date(2026, 11, 19)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        sources_csv=ROOT / "data/sources.csv",
        sources_dir=ROOT / "data/sources",
        audit_dir=tmp_path / "audit",
        export_dir=tmp_path / "export",
        persist_to_postgres=False,
    )


def _report(topic_id: str) -> dict[str, Any]:
    return _audit_report(
        _settings(Path(".")),
        report_date=REPORT_DATE,
        run_id="r",
        records=[],
        coverage=[],
        gaps=[],
        events=[],
        rejected=0,
        topic_id=topic_id,
    )


def test_non_default_topic_gets_its_own_audit_file(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    json_path, _md = _write_audit(settings, _report("ai-policy"))
    assert json_path.parent == settings.audit_dir / "ai-policy"
    assert _load_audit_report(settings, REPORT_DATE, "ai-policy")["topic_id"] == "ai-policy"


def test_check_loader_reads_the_requested_topic(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _write_audit(settings, _report("ukraine-war"))
    _write_audit(settings, _report("ai-policy"))
    for topic in ("ukraine-war", "ai-policy"):
        assert _load_audit_report(settings, REPORT_DATE, topic)["topic_id"] == topic


def test_empty_audit_fails_its_gate() -> None:
    report = _report("ukraine-war")
    assert report["checks"]["records_nonempty"] is False
    assert report["checks"]["gates_pass"] is False


def test_language_accuracy_fails_when_unmeasurable(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    import contextlib
    import io

    import media_quotes.cli.eval as eval_cli

    settings = Settings(
        _env_file=None,
        gold_set_dir=ROOT / "data/gold_set",
        audit_dir=tmp_path / "audit",
        export_dir=tmp_path / "export",
        persist_to_postgres=False,
    )
    monkeypatch.setattr(
        eval_cli,
        "detect_language",
        lambda text: (_ for _ in ()).throw(ValueError("detector unavailable")),
    )
    with contextlib.redirect_stdout(io.StringIO()):
        metrics = eval_cli.evaluate_gold_set_report(
            settings, gold_set="synthetic-v1", report_date=REPORT_DATE
        )
    assert metrics["language_accuracy"] == 0.0
    assert metrics["pass"] is False
    assert "language_accuracy" in metrics["failed_checks"]


def test_cli_reconfigures_console_to_utf8(monkeypatch: Any) -> None:
    from media_quotes.cli import app

    calls: list[dict[str, str]] = []

    class Stream:
        def reconfigure(self, **kwargs: str) -> None:
            calls.append(kwargs)

    monkeypatch.setattr(app.sys, "stdout", Stream())
    monkeypatch.setattr(app.sys, "stderr", Stream())
    app._ensure_utf8_output()
    assert calls == [{"encoding": "utf-8", "errors": "replace"}] * 2


def test_fetch_parser_has_no_inert_dry_run() -> None:
    args = _build_parser().parse_args(["fetch", "--source-id", "guardian"])
    assert not hasattr(args, "dry_run")
    assert args.live is False
