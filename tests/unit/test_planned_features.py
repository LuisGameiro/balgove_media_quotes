"""Tests for the previously planned DB-backed paths now wired in."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from media_quotes.cli import app, ops
from media_quotes.cli.common import _load_registry
from media_quotes.config import Settings
from media_quotes.llm_cache import DbDecisionCache
from media_quotes.llm_live import LLMDecision, LLMRequest

ROOT = Path(__file__).resolve().parents[2]


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "sources_csv": ROOT / "data/sources.csv",
        "sources_dir": ROOT / "data/sources",
        "sections_csv": ROOT / "data/sections.csv",
        "schema_path": ROOT / "data/quotes.schema.json",
        "fixtures_dir": ROOT / "data/fixtures",
        "gold_set_dir": ROOT / "data/gold_set",
        "audit_dir": tmp_path / "audit",
        "export_dir": tmp_path / "export",
        "persist_to_postgres": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def _request() -> LLMRequest:
    return LLMRequest(
        decision_type="section-inference",
        cache_key="k1",
        input_hash="in",
        model_snapshot="model-v1",
        prompt_version="prompt-v1",
        schema_hash="schema-v1",
        system_prompt="system",
        user_prompt="user",
    )


def test_db_decision_cache_roundtrip(tmp_path: Path, monkeypatch: Any) -> None:
    import media_quotes.db.repository as repo
    import media_quotes.pipeline as pipeline

    store: dict[str, dict[str, Any]] = {}
    session = MagicMock()

    @contextmanager
    def fake_context(settings: Settings) -> Any:
        yield session

    monkeypatch.setattr(pipeline, "_default_session_context", fake_context)
    monkeypatch.setattr(repo, "get_decision_cache", lambda s, key: store.get(key), raising=False)

    def fake_upsert(s: Any, entry: Any, *, commit: bool = True) -> bool:
        store[entry.cache_key] = entry.model_dump(mode="json")
        return True

    monkeypatch.setattr(repo, "upsert_decision_cache", fake_upsert, raising=False)

    cache = DbDecisionCache(_settings(tmp_path))
    assert cache.lookup(_request()) is None
    decision = LLMDecision(output={"text": "summary"}, provider_response_id="p1")
    cache.write(_request(), decision)
    restored = cache.lookup(_request())
    assert restored is not None
    assert restored.output == {"text": "summary"}
    assert restored.provider_response_id == "p1"
    assert restored.cached is True


def test_export_from_db_flag(tmp_path: Path, monkeypatch: Any) -> None:
    import media_quotes.db.repository as repo
    from media_quotes.models import ExportQuoteRecord

    record = ExportQuoteRecord.model_validate(
        json.loads((ROOT / "data/quotes.example.jsonl").read_text(encoding="utf-8").splitlines()[0])
    )
    monkeypatch.setattr(repo, "read_export_records", lambda *a, **k: [record], raising=False)
    monkeypatch.setattr(app, "get_settings", lambda: _settings(tmp_path))
    target = tmp_path / "from-db.jsonl"
    assert app.main(["export", "--from-db", "--date", "2026-11-19", "--out", str(target)]) == 0
    assert target.is_file()


def test_backfill_persists_estimate(tmp_path: Path, monkeypatch: Any) -> None:
    import media_quotes.db.repository as repo
    import media_quotes.pipeline as pipeline

    calls: list[str] = []
    session = MagicMock()

    @contextmanager
    def fake_context(settings: Settings) -> Any:
        yield session

    monkeypatch.setattr(pipeline, "_default_session_context", fake_context)
    monkeypatch.setattr(
        repo,
        "start_pipeline_run",
        lambda *a, **k: calls.append("start"),
        raising=False,
    )
    monkeypatch.setattr(
        repo,
        "finish_pipeline_run",
        lambda *a, **k: calls.append("finish"),
        raising=False,
    )
    settings = _settings(tmp_path)
    assert (
        ops._do_backfill(
            settings,
            start=date(2026, 11, 15),
            end=date(2026, 11, 19),
            dry_run=True,
            max_cost=100.0,
        )
        == 0
    )
    assert calls == ["start", "finish"]


def _backfill_settings(tmp_path: Path) -> Settings:
    return _settings(tmp_path, persist_to_postgres=False, report_date=date(2026, 11, 19))


def _spy_backfill_pipeline(
    tmp_path: Path, monkeypatch: Any
) -> tuple[Settings, list[dict[str, Any]]]:
    settings = _backfill_settings(tmp_path)
    monkeypatch.setattr(app, "get_settings", lambda: settings)
    calls: list[dict[str, Any]] = []

    def fake_run(settings_arg: Settings, **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(records=[], gaps=[], persistence={})

    monkeypatch.setattr("media_quotes.pipeline.run_daily_pipeline", fake_run)
    return settings, calls


def test_backfill_rejects_unknown_source_id_before_estimate(
    tmp_path: Path, monkeypatch: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _backfill_settings(tmp_path)
    monkeypatch.setattr(app, "get_settings", lambda: settings)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("estimate must not run for an unknown source id")

    monkeypatch.setattr(ops, "estimate_backfill", forbidden)
    rc = app.main(["backfill", "--source-id", "does-not-exist"])
    assert rc == 3
    assert "unknown --source-id: does-not-exist" in capsys.readouterr().err


def test_backfill_accepts_explicit_tier4_source_id(tmp_path: Path, monkeypatch: Any) -> None:
    _, calls = _spy_backfill_pipeline(tmp_path, monkeypatch)
    rc = app.main(
        [
            "backfill",
            "--from",
            "2026-11-18",
            "--to",
            "2026-11-19",
            "--source-id",
            "rt",
            "--max-cost",
            "500",
            "--execute",
        ]
    )
    assert rc in (0, 2)
    assert len(calls) == 2
    assert all(call["source_ids"] == {"rt"} for call in calls)
    assert all(call["source_mode"] == "fixtures" for call in calls)


def test_backfill_without_ids_excludes_tier4_and_passes_resolved_ids(
    tmp_path: Path, monkeypatch: Any
) -> None:
    settings, calls = _spy_backfill_pipeline(tmp_path, monkeypatch)
    expected = {row.source_id for row in _load_registry(settings) if row.tier < 4}
    rc = app.main(
        [
            "backfill",
            "--from",
            "2026-11-18",
            "--to",
            "2026-11-19",
            "--max-cost",
            "500",
            "--execute",
        ]
    )
    assert rc in (0, 2)
    assert len(calls) == 2
    assert all(call["source_ids"] == expected for call in calls)
    assert not any(
        row.source_id in calls[0]["source_ids"] for row in _load_registry(settings) if row.tier == 4
    )


def test_backfill_rejects_span_over_max_days(
    tmp_path: Path, monkeypatch: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _backfill_settings(tmp_path)
    monkeypatch.setattr(app, "get_settings", lambda: settings)

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("estimate must not run for an over-long span")

    monkeypatch.setattr(ops, "estimate_backfill", forbidden)
    rc = app.main(["backfill", "--from", "2025-01-01", "--to", "2026-11-19"])
    assert rc == 3
    assert "backfill span exceeds the 365-day limit" in capsys.readouterr().err
