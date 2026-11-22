"""Tests for the previously planned DB-backed paths now wired in."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from media_quotes.cli import app, ops
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
