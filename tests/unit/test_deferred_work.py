"""Tests for partitioning registration, static extraction fallback and batch plumbing."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
from pydantic import SecretStr

from media_quotes.batch_runner import BatchJob, OpenRouterBatchClient
from media_quotes.cli import app
from media_quotes.config import Settings
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
    assert batch_client.is_terminal(job) is True
    assert job.results[0]["custom_id"] == "decision-x"


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
    client.is_terminal.return_value = True
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
    applied = batch_cli._apply_results(settings, [result], {"decision-x": "cache-k"})
    assert applied == 1
    assert written[0].cache_key == "cache-k"
    assert written[0].output == {"text": "summary"}


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
    monkeypatch.setattr(app, "get_settings", lambda: _settings(tmp_path))
    assert app.main(["batch", "--requests", str(path), "--max-cost", "10"]) == 0
    assert path.with_suffix(".batch.json").is_file()
