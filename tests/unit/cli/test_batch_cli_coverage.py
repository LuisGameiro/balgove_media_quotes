"""Unit tests for edge cases in media_quotes.cli.batch and media_quotes.processing.export."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from media_quotes.ai.llm_live import LLMRequest
from media_quotes.cli.batch import (
    _apply_results,
    _extract_text,
    _load_requests,
    _require_matching_model,
)
from media_quotes.exceptions import ConfigError
from media_quotes.processing.export import safe_replace as _safe_replace


def _make_req(model: str = "openai/gpt-4o-mini-2024-07-18") -> LLMRequest:
    return LLMRequest(
        decision_type="section_inference",
        cache_key="k1",
        input_hash="i",
        model_snapshot=model,
        prompt_version="p1",
        schema_hash="s1",
        system_prompt="Return JSON only",
        user_prompt="<quote>data</quote>",
    )


def test_status_apply_without_execute_never_writes(tmp_path: Path) -> None:
    from media_quotes.cli.batch import _do_batch
    from media_quotes.config import Settings

    settings = Settings(_env_file=None, persist_to_postgres=True)
    with pytest.raises(ConfigError, match="--execute"):
        _do_batch(
            settings,
            requests_path=tmp_path / "missing.json",
            max_cost=None,
            execute=False,
            status_id="batch-1",
            dry_run=True,
            apply=True,
        )


def test_load_requests_rejects_invalid_json_type(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.json"
    bad_file.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="must be a JSON array"):
        _load_requests(bad_file)


def test_require_matching_model() -> None:
    reqs = [_make_req("model-a"), _make_req("model-b")]
    with pytest.raises(ConfigError, match="BATCH_MODEL_SNAPSHOT"):
        _require_matching_model(reqs, "model-a")

    # Matching models should not raise
    matching = [_make_req("model-a"), _make_req("model-a")]
    _require_matching_model(matching, "model-a")


def test_extract_text_variations() -> None:
    # Top-level non-dict or error
    assert _extract_text("not-a-dict") is None  # type: ignore[arg-type]
    assert _extract_text({"error": "something failed"}) is None

    # Missing or invalid response
    assert _extract_text({}) is None
    assert _extract_text({"response": "not-dict"}) is None
    assert _extract_text({"response": {"status_code": 500}}) is None

    # Body errors
    assert _extract_text({"response": {"status_code": 200, "body": "string"}}) is None
    assert _extract_text({"response": {"status_code": 200, "body": {"error": "bad"}}}) is None
    assert _extract_text({"response": {"status_code": 200, "body": {"refusal": True}}}) is None

    # Responses API: output[].content[].text
    resp_api = {
        "response": {
            "status_code": 200,
            "body": {
                "output": [
                    {
                        "content": [
                            {"type": "output_text", "text": "Hello "},
                            {"type": "other", "text": "ignored"},
                            {"type": "output_text", "text": "World"},
                        ]
                    }
                ]
            },
        }
    }
    assert _extract_text(resp_api) == "Hello World"

    # Chat Completions: body["output_text"]
    assert (
        _extract_text({"response": {"status_code": 200, "body": {"output_text": "Direct output"}}})
        == "Direct output"
    )

    # Choices with content_filter
    assert (
        _extract_text(
            {
                "response": {
                    "status_code": 200,
                    "body": {"choices": [{"finish_reason": "content_filter"}]},
                }
            }
        )
        is None
    )


def test_apply_results_never_caches_plain_text_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    from collections.abc import Iterator
    from contextlib import contextmanager
    from unittest.mock import MagicMock

    from media_quotes.batch import custom_id
    from media_quotes.config import Settings

    @contextmanager
    def fake_context(_settings: object) -> Iterator[MagicMock]:
        yield MagicMock()

    written: list[object] = []
    monkeypatch.setattr("media_quotes.pipeline.default_session_context", fake_context)
    monkeypatch.setattr(
        "media_quotes.db.repository.upsert_decision_cache",
        lambda session, entry, **kwargs: written.append(entry),
        raising=False,
    )
    request = _make_req()
    result = {
        "custom_id": custom_id(request.cache_key),
        "response": {"body": {"output_text": "I refuse to answer this request."}},
    }
    applied, unmatched = _apply_results(Settings(_env_file=None), [result], [request])
    assert (applied, unmatched) == (0, 0)
    assert written == []

    # Choices with refusal
    assert (
        _extract_text(
            {
                "response": {
                    "status_code": 200,
                    "body": {"choices": [{"message": {"refusal": "I cannot"}}]},
                }
            }
        )
        is None
    )

    # Choices with valid content
    assert (
        _extract_text(
            {
                "response": {
                    "status_code": 200,
                    "body": {"choices": [{"message": {"content": "Choice content"}}]},
                }
            }
        )
        == "Choice content"
    )

    # Unrecognized empty body
    assert _extract_text({"response": {"status_code": 200, "body": {}}}) is None


def test_safe_replace_retries_on_permission_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    src = tmp_path / "source.txt"
    dst = tmp_path / "dest.txt"
    src.write_text("content", encoding="utf-8")

    attempts = 0
    orig_replace = os.replace

    def mock_replace(s: os.PathLike[str] | str, d: os.PathLike[str] | str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("Access is denied")
        orig_replace(s, d)

    monkeypatch.setattr(os, "replace", mock_replace)
    _safe_replace(src, dst)

    assert dst.read_text(encoding="utf-8") == "content"
    assert attempts == 3
