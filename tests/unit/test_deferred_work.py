"""Tests for partitioning registration, static extraction fallback and batch plumbing."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from media_quotes.batch_runner import BatchJob, OpenRouterBatchClient
from media_quotes.cli import app
from media_quotes.config import Settings
from media_quotes.exceptions import ConfigError
from media_quotes.extraction import extract_article, quality_gate
from media_quotes.risk_controls.retention import retention_plan

ROOT = Path(__file__).resolve().parents[2]


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "sources_csv": ROOT / "data/sources.csv",
        "sources_dir": ROOT / "data/sources",
        "sections_csv": ROOT / "data/sections.csv",
        "schema_path": ROOT / "data/quotes.schema.json",
        "fixtures_dir": ROOT / "data/fixtures",
        "audit_dir": tmp_path / "audit",
        "export_dir": tmp_path / "export",
        "persist_to_postgres": False,
        "openrouter_api_key": SecretStr("k"),
        "llm_provider": "openrouter",
        "llm_model_snapshot": "openai/gpt-4o-mini-2024-07-18",
        "batch_model_snapshot": "openai/gpt-4o-mini-2024-07-18:batch",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_parse_replaces_invalid_utf8() -> None:
    from media_quotes.adapters.base import SourceAdapter

    result = SourceAdapter._parse(
        b"<html><body><article>caf\xe9 summary</article></body></html>",
        use_trafilatura=True,
        source_url="https://example.invalid/a",
    )
    assert isinstance(result, dict)


def test_partition_plan_names_only_audit_events() -> None:
    from media_quotes.risk_controls.models import RetentionPolicy

    plan = retention_plan(
        RetentionPolicy(), date(2026, 11, 20), [date(2026, 10, 1), date(2026, 11, 1)]
    )
    assert all(name.startswith("audit_events_") for name in plan.detach + plan.keep_hot)


def test_static_fallback_is_wired_through_extraction() -> None:
    from media_quotes.extraction import ExtractionParams

    long_text = ("words " * 150).strip()
    calls: list[str] = []

    def fake_first_pass(raw_html: str, **kwargs: Any) -> dict[str, str]:
        calls.append("first")
        return {"text": "short"}

    # Patch the module-level import by intercepting trafilatura for the first pass only.
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import trafilatura

    original = trafilatura.bare_extraction
    try:
        trafilatura.bare_extraction = fake_first_pass  # type: ignore[assignment]
        result = extract_article(
            "<html><body><article><p>" + long_text + "</p></article></body></html>",
            params=ExtractionParams(),
            source_url="https://example.invalid/a",
            fallback=lambda raw_html, *, source_url: long_text,
        )
    finally:
        trafilatura.bare_extraction = original  # type: ignore[assignment]
    assert calls == ["first"]
    assert result.used_fallback is True
    assert result.quality_ok is True
    assert quality_gate(long_text)[0] is True


def test_batch_client_submit_and_status_with_mock_transport() -> None:

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"id": "batch-1"})
        return httpx.Response(
            200,
            json={
                "id": "batch-1",
                "status": "completed",
                "request_counts": {"total": 1, "completed": 1},
                "results": [
                    {"custom_id": "decision-x", "response": {"body": {"output_text": "ok"}}}
                ],
            },
        )

    settings = _settings(Path("."))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        batch_client = OpenRouterBatchClient(settings, client=client)
        batch_id = batch_client.submit({"endpoint": "/v1/responses", "requests": []})
        job = batch_client.status(batch_id)
    assert batch_id == "batch-1"
    assert job.status == "completed"
    assert job.results[0]["custom_id"] == "decision-x"


def test_batch_status_rejects_path_like_id() -> None:
    client = OpenRouterBatchClient(_settings(Path(".")), client=MagicMock())
    with pytest.raises(ConfigError, match="invalid batch id"):
        client.status("../models")
    client.close()


def test_batch_cli_status_uses_client(monkeypatch: Any) -> None:
    from media_quotes.cli import batch as batch_cli

    job = BatchJob(
        id="batch-1",
        status="completed",
        request_counts={"total": 1, "completed": 1},
        results=[],
    )
    client = MagicMock()
    client.status.return_value = job
    monkeypatch.setattr(batch_cli, "OpenRouterBatchClient", lambda settings: client)
    assert (
        batch_cli._do_batch(
            _settings(Path(".")),
            requests_path=Path("."),
            max_cost=None,
            execute=False,
            status_id="batch-1",
            dry_run=False,
        )
        == 0
    )
    client.status.assert_called_once_with("batch-1")
    client.close.assert_called_once_with()


def test_batch_apply_results_writes_cache(tmp_path: Path, monkeypatch: Any) -> None:
    from contextlib import contextmanager

    from media_quotes.cli import batch as batch_cli

    session = MagicMock()

    @contextmanager
    def fake_context(settings: Settings) -> Any:
        yield session

    monkeypatch.setattr("media_quotes.pipeline._default_session_context", fake_context)
    written: list[Any] = []
    monkeypatch.setattr(
        "media_quotes.db.repository.upsert_decision_cache",
        lambda s, entry: written.append(entry),
        raising=False,
    )
    settings = _settings(tmp_path, persist_to_postgres=True)
    result = {
        "custom_id": "decision-x",
        "response": {"body": {"output_text": "summary"}},
    }
    from media_quotes.batch import custom_id
    from media_quotes.llm_live import LLMRequest

    request = LLMRequest(
        decision_type="section-inference",
        cache_key="cache-k",
        input_hash="input-k",
        model_snapshot="openai/gpt-4o-mini-2024-07-18",
        prompt_version="p1",
        schema_hash="s1",
        system_prompt="system",
        user_prompt="user",
    )
    result["custom_id"] = custom_id("cache-k")
    applied, unmatched = batch_cli._apply_results(settings, [result], [request])
    assert (applied, unmatched) == (1, 0)
    assert written[0].cache_key == "cache-k"
    assert written[0].model_snapshot == request.model_snapshot
    assert written[0].prompt_version == "p1"
    assert written[0].schema_hash == "s1"
    assert written[0].output == {"text": "summary"}


def test_batch_apply_reports_unmatched_results(tmp_path: Path, monkeypatch: Any) -> None:
    from contextlib import contextmanager

    from media_quotes.cli import batch as batch_cli
    from media_quotes.llm_live import LLMRequest

    session = MagicMock()

    @contextmanager
    def fake_context(settings: Settings) -> Any:
        yield session

    monkeypatch.setattr("media_quotes.pipeline._default_session_context", fake_context)
    request = LLMRequest(
        decision_type="section-inference",
        cache_key="cache-k",
        input_hash="i",
        model_snapshot="openai/gpt-4o-mini-2024-07-18",
        prompt_version="p1",
        schema_hash="s1",
        system_prompt="system",
        user_prompt="user",
    )
    result = {"custom_id": "unmatched", "response": {"body": {"output_text": "x"}}}
    applied, unmatched = batch_cli._apply_results(_settings(tmp_path), [result], [request])
    assert (applied, unmatched) == (0, 1)


def test_batch_cli_dry_run_writes_no_network(tmp_path: Path, monkeypatch: Any) -> None:
    requests = [
        {
            "decision_type": "section-inference",
            "cache_key": "k1",
            "input_hash": "i",
            "model_snapshot": "openai/gpt-4o-mini-2024-07-18",
            "prompt_version": "p1",
            "schema_hash": "s1",
            "system_prompt": "system",
            "user_prompt": "user",
        }
    ]
    path = tmp_path / "requests.json"
    path.write_text(json.dumps(requests), encoding="utf-8")
    monkeypatch.setattr(
        app, "get_settings", lambda: _settings(tmp_path, batch_endpoint="/v1/chat/completions")
    )
    assert app.main(["batch", "--requests", str(path), "--max-cost", "10"]) == 0
    out = path.with_suffix(".batch.json")
    assert out.is_file()
    assert json.loads(out.read_text(encoding="utf-8"))["endpoint"] == "/v1/chat/completions"


def test_extract_text_reads_responses_shape() -> None:
    from media_quotes.cli.batch import _extract_text

    result = {
        "response": {
            "body": {
                "object": "response",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "positive"},
                            {"type": "output_text", "text": " negative"},
                        ],
                    }
                ],
            }
        }
    }
    assert _extract_text(result) == "positive negative"
    assert _extract_text({"response": None}) is None


def test_batch_load_requests_rejects_non_dict(tmp_path: Path) -> None:
    from media_quotes.cli.batch import _load_requests

    path = tmp_path / "bad.json"
    path.write_text(json.dumps(["not-an-object"]), encoding="utf-8")
    with pytest.raises(ValidationError):
        _load_requests(path)


def test_batch_status_failed_returns_failure(monkeypatch: Any) -> None:
    from media_quotes.cli import batch as batch_cli

    job = BatchJob(
        id="batch-1",
        status="failed",
        request_counts={"total": 1, "failed": 1},
        results=[],
    )
    client = MagicMock()
    client.status.return_value = job
    monkeypatch.setattr(batch_cli, "OpenRouterBatchClient", lambda settings: client)
    assert (
        batch_cli._do_batch(
            _settings(Path(".")),
            requests_path=Path("."),
            max_cost=None,
            execute=False,
            status_id="batch-1",
            dry_run=False,
        )
        == 1
    )


def test_batch_apply_requires_requests(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(app, "get_settings", lambda: _settings(tmp_path))
    assert app.main(["batch", "--status", "batch-1", "--apply"]) == 3


def test_execute_rejects_model_mismatch(tmp_path: Path) -> None:
    from media_quotes.cli.batch import _do_batch

    request = {
        "decision_type": "section-inference",
        "cache_key": "k1",
        "input_hash": "i",
        "model_snapshot": "openai/gpt-4o-mini-2024-07-18",
        "prompt_version": "p1",
        "schema_hash": "s1",
        "system_prompt": "system",
        "user_prompt": "user",
    }
    path = tmp_path / "requests.json"
    path.write_text(json.dumps([request]), encoding="utf-8")
    settings = _settings(tmp_path, batch_model_snapshot="anthropic/claude-3-5-haiku:batch")
    with pytest.raises(ConfigError, match="model_snapshot"):
        _do_batch(
            settings,
            requests_path=path,
            max_cost=10.0,
            execute=True,
            status_id=None,
            dry_run=False,
        )


def test_batch_submit_without_postgres_never_opens_db(tmp_path: Path, monkeypatch: Any) -> None:
    from media_quotes.cli import batch as batch_cli

    request = {
        "decision_type": "section-inference",
        "cache_key": "k1",
        "input_hash": "i",
        "model_snapshot": "openai/gpt-4o-mini-2024-07-18",
        "prompt_version": "p1",
        "schema_hash": "s1",
        "system_prompt": "system",
        "user_prompt": "user",
    }
    path = tmp_path / "requests.json"
    path.write_text(json.dumps([request]), encoding="utf-8")
    settings = _settings(tmp_path, batch_model_snapshot="openai/gpt-4o-mini-2024-07-18")
    client = MagicMock()
    client.submit.return_value = "batch-1"
    monkeypatch.setattr(batch_cli, "OpenRouterBatchClient", lambda _settings: client)

    def forbid_db(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("batch submission must not touch Postgres when persistence is off")

    monkeypatch.setattr("media_quotes.pipeline._default_session_context", forbid_db)
    assert (
        batch_cli._do_batch(
            settings,
            requests_path=path,
            max_cost=10.0,
            execute=True,
            status_id=None,
            dry_run=False,
        )
        == 0
    )
    client.submit.assert_called_once()
